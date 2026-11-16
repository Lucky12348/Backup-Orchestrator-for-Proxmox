import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, HardDrive, Loader2, Play, RefreshCw, Search, Unplug } from "lucide-react";
import { useLocation } from "react-router-dom";

import { decideDisk, getExternalBackupReadiness, rescanDisks, type DiskDecision, type ExternalBackupReadiness } from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { focusedDiskId } from "../../liveDisks";
import { ApiError } from "../../notices";
import { readinessTitle } from "../../readiness";
import { openRunModal } from "../../runs/openRun";
import type { ExternalBackupRun, ExternalDisk } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { InfoTip } from "../../ui/InfoTip";
import { useToast } from "../../ui/toast";
import { fill, formatSize, formatWhen } from "../format";
import {
  DISK_TABS,
  dataWarning,
  diskActions,
  diskHistory,
  diskRows,
  nameConfirmed,
  rejectReason,
  STATUS_TONE,
  tabCounts,
  usagePercent,
  visibleRows,
  type DiskRow,
  type DiskTab,
  type DiskUiStatus,
  type Tone,
} from "./diskModel";

const TONE: Record<Tone, { pill: string; dot: string; icon: string; bar: string; note: string }> = {
  ok: { pill: "bg-success/15 text-success-text", dot: "bg-success", icon: "bg-success/15 text-success-text", bar: "bg-accent", note: "text-muted" },
  run: { pill: "bg-accent/15 text-accent-text", dot: "bg-accent", icon: "bg-accent/15 text-accent-text", bar: "bg-accent", note: "text-muted" },
  warn: { pill: "bg-warning/15 text-warning-text", dot: "bg-warning", icon: "bg-warning/15 text-warning-text", bar: "bg-warning", note: "text-warning-text" },
  err: { pill: "bg-danger/15 text-danger-text", dot: "bg-danger", icon: "bg-danger/15 text-danger-text", bar: "bg-danger", note: "text-danger-text" },
  off: { pill: "bg-subtle text-muted", dot: "bg-faint", icon: "bg-subtle text-muted", bar: "bg-accent", note: "text-muted" },
};

const HISTORY_DOT: Record<Tone, string> = { ok: "bg-success", run: "bg-accent", warn: "bg-warning", err: "bg-danger", off: "bg-faint" };

export interface DisksViewProps {
  disks: ExternalDisk[];
  runs: ExternalBackupRun[];
  language: Language;
  t: TranslationDictionary;
  /** Opens the backup confirmation (App.tsx) — the request itself is guarded there. */
  onBackup: (disk: ExternalDisk) => void;
  onEject: (disk: ExternalDisk) => void;
  isStarting: (diskId: number) => boolean;
  isEjecting: (diskId: number) => boolean;
  onChanged: () => Promise<void> | void;
}

export function StatusPill({ status, label }: { status: DiskUiStatus; label: string }) {
  const tone = TONE[STATUS_TONE[status]];
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full py-[3px] pl-2 pr-2.5 text-xs font-medium", tone.pill)} data-status={status}>
      <span className={cn("h-1.5 w-1.5 rounded-full", tone.dot)} />
      {label}
    </span>
  );
}

/** /disks?tab=approved (dashboard "Start a backup"). */
function initialTab(search: string): DiskTab {
  const value = new URLSearchParams(search).get("tab");
  return DISK_TABS.includes(value as DiskTab) ? (value as DiskTab) : "all";
}

function errorText(error: unknown, t: TranslationDictionary): string {
  const errors = t.disksV2.errors as Record<string, string>;
  if (error instanceof ApiError && error.code && errors[error.code]) return errors[error.code];
  return error instanceof Error ? error.message : t.error;
}

export function DisksView({ disks, runs, language, t, onBackup, onEject, isStarting, isEjecting, onChanged }: DisksViewProps) {
  const text = t.disksV2;
  const { toast } = useToast();
  const search = useLocation().search;
  const [tab, setTab] = useState<DiskTab>(() => initialTab(search));
  const [query, setQuery] = useState("");
  const [rescanning, setRescanning] = useState(false);
  const focusId = focusedDiskId(search);
  const rows = useMemo(() => diskRows(disks, runs), [disks, runs]);
  const counts = tabCounts(rows);
  const shown = visibleRows(rows, tab, query);
  const [selectedId, setSelectedId] = useState<number | null>(focusId);

  useEffect(() => {
    if (focusId) setSelectedId(focusId);
  }, [focusId]);
  useEffect(() => {
    // Keep a selection: the focused disk, else the first visible card.
    if (!shown.some((row) => row.disk.id === selectedId) && !rows.some((row) => row.disk.id === selectedId)) {
      setSelectedId(shown[0]?.disk.id ?? null);
    }
  }, [shown, rows, selectedId]);
  const selected = rows.find((row) => row.disk.id === selectedId) ?? shown[0];

  async function rescan() {
    setRescanning(true);
    try {
      await rescanDisks();
      toast({ tone: "success", title: text.rescanDone });
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    } finally {
      setRescanning(false);
    }
  }

  return (
    <div className="flex flex-col gap-[18px]">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{text.title}</h1>
        <InfoTip text={text.info} label={t.shell.about} />
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void rescan()}
          disabled={rescanning}
          className="flex items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3.5 py-[9px] text-sm font-medium text-fg hover:brightness-110 disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", rescanning && "animate-spin")} />
          {text.rescan}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-b border-solid border-border">
        <div role="tablist" aria-label={text.tabsLabel} className="-mb-px flex min-w-0 flex-1 gap-[22px] overflow-x-auto">
          {DISK_TABS.map((key) => {
            const active = tab === key;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(key)}
                className={cn(
                  "flex shrink-0 items-center gap-2 whitespace-nowrap border-0 bg-transparent px-0 pb-3 pt-2.5 text-sm font-medium",
                  active ? "text-fg shadow-[inset_0_-2px_0_rgb(var(--pbo-accent))]" : "text-faint hover:text-fg",
                )}
              >
                {text.tabs[key]}
                <span
                  className={cn(
                    "min-w-[18px] rounded-full px-1.5 py-px text-[11px] font-semibold",
                    key === "review" ? "bg-warning/15 text-warning-text" : active ? "bg-accent/15 text-accent-text" : "bg-subtle text-faint",
                  )}
                >
                  {counts[key]}
                </span>
              </button>
            );
          })}
        </div>
        <label className="mb-2 flex h-8 w-full items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-2.5 text-faint sm:w-[220px]">
          <Search className="h-3.5 w-3.5" />
          <input
            aria-label={text.search}
            placeholder={text.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-fg outline-none placeholder:text-faint"
          />
        </label>
      </div>

      {/* B27: cards fill the width (auto-fill), the detail panel sits right next to them. */}
      {/* Cards and panel side by side with no hole: fixed 380 px panel, cards
          auto-fit (empty tracks collapse, the cards fill the row). */}
      <div className="grid items-start gap-[18px] lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-3.5">
          {shown.length === 0 ? (
            <div className="col-span-full rounded-[14px] border border-dashed border-border-strong px-6 py-10 text-center">
              <p className="m-0 text-[15px] font-medium">{query ? text.noMatch : text.empty}</p>
              {query ? null : <p className="m-0 mt-1.5 text-[13px] text-muted">{text.emptyHint}</p>}
            </div>
          ) : (
            shown.map((row) => (
              <DiskCard
                key={row.disk.id}
                row={row}
                selected={selected?.disk.id === row.disk.id}
                onSelect={() => setSelectedId(row.disk.id)}
                language={language}
                t={t}
              />
            ))
          )}
        </div>
        {selected ? (
          <DiskDetail
            key={selected.disk.id}
            row={selected}
            runs={runs}
            language={language}
            t={t}
            onBackup={onBackup}
            onEject={onEject}
            starting={isStarting(selected.disk.id)}
            ejecting={isEjecting(selected.disk.id)}
            onRescan={() => void rescan()}
            rescanning={rescanning}
            onChanged={onChanged}
          />
        ) : (
          <aside className="rounded-[14px] border border-solid border-border bg-surface p-[18px] text-[13px] text-muted lg:sticky lg:top-4">
            {text.selectHint}
          </aside>
        )}
      </div>
    </div>
  );
}

function capacityLine(row: DiskRow<ExternalDisk>, language: Language, t: TranslationDictionary): string {
  const disk = row.disk;
  if (disk.filesystem_used_gb != null && disk.filesystem_total_gb) {
    return fill(t.disksV2.usedOf, { used: formatSize(disk.filesystem_used_gb, language), total: formatSize(disk.filesystem_total_gb, language) });
  }
  if (row.status === "new") {
    const warning = dataWarning(disk);
    if (warning.hasData) return `${t.disksV2.approval.dataWarning}${warning.filesystem ? ` (${warning.filesystem})` : ""}`;
  }
  return "";
}

function noteText(row: DiskRow<ExternalDisk>, t: TranslationDictionary): string {
  const reason = rejectReason(row.disk);
  if (row.status === "rejected" && reason) return fill(t.disksV2.rejectReason[reason as keyof typeof t.disksV2.rejectReason] ?? "", { min: 32 });
  if (row.status === "running" && row.run?.progress_percent != null) return `${t.disksV2.status.running} · ${Math.round(row.run.progress_percent)} %`;
  return t.disksV2.summary[row.status];
}

export function DiskCard({
  row,
  selected,
  onSelect,
  language,
  t,
}: {
  row: DiskRow<ExternalDisk>;
  selected: boolean;
  onSelect: () => void;
  language: Language;
  t: TranslationDictionary;
}) {
  const { disk, status } = row;
  const tone = TONE[STATUS_TONE[status]];
  const usage = usagePercent(disk);
  const tags = [
    status === "new" || status === "approved" || status === "running" || status === "ejected" ? t.disksV2.tagPlugged : null,
    disk.trusted ? t.disksV2.status.approved : null,
    disk.pbs_datastore_name ?? null,
  ].filter((tag): tag is string => Boolean(tag));
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-disk={disk.id}
      className={cn(
        "flex flex-col gap-3 rounded-[14px] border border-solid bg-surface p-4 text-left text-fg",
        selected ? "border-accent shadow-[0_0_0_3px_rgb(var(--pbo-accent)/0.18)]" : "border-border hover:border-border-strong",
      )}
    >
      <div className="flex w-full items-start gap-3">
        <div className={cn("flex h-[38px] w-[38px] flex-none items-center justify-center rounded-[10px]", tone.icon)}>
          <HardDrive className="h-[18px] w-[18px]" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-[15px] font-semibold">{disk.display_name}</span>
          <span className="truncate text-xs text-faint">{disk.model_name ?? disk.serial_number}</span>
        </div>
        <StatusPill status={status} label={t.disksV2.status[status]} />
      </div>
      <div className="flex w-full flex-col gap-1.5">
        <div className="flex gap-2 text-xs text-muted">
          <span className="min-w-0 flex-1 truncate">{capacityLine(row, language, t)}</span>
          <span className="flex-none font-mono">{formatSize(disk.capacity_gb, language)}</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-subtle">
          <div className={cn("h-full rounded-full", tone.bar)} style={{ width: `${usage ?? 0}%` }} />
        </div>
      </div>
      <span className={cn("text-[13px]", tone.note)}>{noteText(row, t)}</span>
      {tags.length ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag) => (
            <span key={tag} className="rounded-md bg-subtle px-2 py-0.5 text-[11px] text-muted">
              {tag}
            </span>
          ))}
        </div>
      ) : null}
    </button>
  );
}

function useReadiness(disk: ExternalDisk, status: DiskUiStatus, runs: ExternalBackupRun[]) {
  const [readiness, setReadiness] = useState<ExternalBackupReadiness | undefined>(undefined);
  const key = `${disk.id}:${status}:${disk.trusted}:${runs.filter((run) => run.status === "pending" || run.status === "running").length}`;
  useEffect(() => {
    let cancelled = false;
    setReadiness(undefined);
    getExternalBackupReadiness(disk.id).then(
      (result) => !cancelled && setReadiness(result),
      () => !cancelled && setReadiness({ ready: false, reasons: [] } as unknown as ExternalBackupReadiness),
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return readiness;
}

const primary = "flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-3.5 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50";
const secondary = "flex items-center justify-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3 py-[9px] text-[13px] font-medium text-fg hover:brightness-110 disabled:cursor-not-allowed disabled:text-muted disabled:hover:brightness-100";

export function DiskDetail({
  row,
  runs,
  language,
  t,
  onBackup,
  onEject,
  starting,
  ejecting,
  onRescan,
  rescanning,
  onChanged,
}: {
  row: DiskRow<ExternalDisk>;
  runs: ExternalBackupRun[];
  language: Language;
  t: TranslationDictionary;
  onBackup: (disk: ExternalDisk) => void;
  onEject: (disk: ExternalDisk) => void;
  starting: boolean;
  ejecting: boolean;
  onRescan: () => void;
  rescanning: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const { disk, status } = row;
  const text = t.disksV2;
  const now = new Date();
  const when = (iso: string | null) => formatWhen(iso, language, now, t.dashboardV2);
  const actions = diskActions(disk, status);
  const readiness = useReadiness(disk, status, runs);
  const history = diskHistory(disk, runs);
  const reason = rejectReason(disk);
  const location = disk.usb_location;
  const technical: [string, string | null | undefined][] = [
    [text.field.serial, disk.serial_number],
    [text.field.reportedSerial, disk.reported_serial_number],
    [text.field.model, disk.model_name],
    [text.field.capacity, formatSize(disk.capacity_gb, language)],
    [text.field.usbPort, location?.port_id],
    [text.field.filesystem, disk.filesystem_type],
    [text.field.datastore, disk.pbs_datastore_name],
    [text.field.handoff, disk.handoff_status],
    [text.field.slot, disk.pbs_handoff_slot],
    [text.field.lastSeen, disk.last_seen_at ? when(disk.last_seen_at) : null],
    [text.field.id, String(disk.id)],
  ];

  return (
    <aside aria-label={text.selected} className="flex min-w-0 flex-col rounded-[14px] border border-solid border-border bg-surface lg:sticky lg:top-4">
      <div className="flex flex-col gap-1 border-b border-solid border-border p-[18px]">
        <span className="text-xs text-faint">{text.selected}</span>
        <span className="text-[17px] font-semibold">{disk.display_name}</span>
        <span className="text-[13px] text-muted">{text.summary[status]}</span>
      </div>

      {status === "new" ? <ApprovalFlow disk={disk} t={t} language={language} onChanged={onChanged} /> : null}

      {status === "rejected" && reason ? (
        <div className="border-b border-solid border-border p-[18px]">
          <div className="rounded-[10px] bg-danger/15 p-3 text-[13px] leading-[1.45] text-danger-text">
            {fill(text.rejectReason[reason as keyof typeof text.rejectReason] ?? "", { min: 32 })}
          </div>
        </div>
      ) : null}

      {actions.length ? (
        <div className="flex flex-col gap-2.5 border-b border-solid border-border p-[18px]">
          {actions.map((action) => {
            switch (action) {
              case "backup": {
                const blocked = !readiness?.ready;
                const title = readinessTitle(readiness, t.readiness as Record<string, string>);
                return (
                  <div key={action} className="flex flex-col gap-1.5">
                    <button type="button" className={primary} disabled={blocked || starting} title={title} onClick={() => onBackup(disk)}>
                      {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      {starting ? text.action.starting : text.action.backup}
                    </button>
                    {readiness && !readiness.ready && title ? <span className="whitespace-pre-line text-xs text-faint">{title}</span> : null}
                  </div>
                );
              }
              case "follow":
                return (
                  <button key={action} type="button" className={primary} onClick={() => row.run && openRunModal(row.run.id)}>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {row.run?.progress_percent != null ? fill(text.action.followPct, { pct: Math.round(row.run.progress_percent) }) : text.action.follow}
                  </button>
                );
              case "eject":
                return (
                  <button key={action} type="button" className={secondary} disabled={status === "running" || ejecting} onClick={() => onEject(disk)}>
                    <Unplug className="h-4 w-4" />
                    {status === "running" ? text.action.ejectAfter : text.action.eject}
                  </button>
                );
              case "replug":
                return (
                  <div key={action} role="status" className="flex items-center gap-2.5 rounded-[10px] bg-subtle p-3 text-[13px] font-medium text-fg">
                    <Unplug className="h-4 w-4 flex-none text-muted" />
                    {text.action.replug}
                  </div>
                );
              case "rescan":
                return (
                  <button key={action} type="button" className={secondary} disabled={rescanning} onClick={onRescan}>
                    <RefreshCw className={cn("h-4 w-4", rescanning && "animate-spin")} />
                    {text.action.rescan}
                  </button>
                );
              case "unreject":
                return <DecisionButton key={action} disk={disk} decision="clear" label={text.action.unreject} toastText={text.approval.unrejectedToast} t={t} onChanged={onChanged} />;
            }
          })}
        </div>
      ) : null}

      <div className="px-[18px] pb-1.5 pt-3.5 text-[13px] font-semibold">{text.history}</div>
      {history.length === 0 ? (
        <div className="px-[18px] py-1.5 text-[13px] text-faint">{text.noHistory}</div>
      ) : (
        history.map((item) => (
          <div key={item.id} className="flex items-center gap-2.5 px-[18px] py-1.5 text-[13px]">
            <span className={cn("h-[7px] w-[7px] flex-none rounded-full", HISTORY_DOT[item.tone])} />
            <span className="flex-1 text-muted">
              {text.historyKind[item.kind]} · {(text.historyStatus as Record<string, string>)[item.status] ?? item.status}
            </span>
            <span className="text-xs text-faint">{when(item.at)}</span>
          </div>
        ))
      )}

      <details className="mt-2.5 border-t border-solid border-border">
        <summary className="cursor-pointer px-[18px] py-3.5 text-[13px] font-medium text-muted">{text.technical}</summary>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 px-[18px] pb-4 text-xs">
          {technical
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label} className="contents">
                <span className="text-faint">{label}</span>
                <span className="font-mono text-muted [overflow-wrap:anywhere]">{value}</span>
              </div>
            ))}
        </div>
      </details>
    </aside>
  );
}

function DecisionButton({
  disk,
  decision,
  label,
  toastText,
  t,
  onChanged,
  confirmFirst,
}: {
  disk: ExternalDisk;
  decision: DiskDecision;
  label: string;
  toastText: string;
  t: TranslationDictionary;
  onChanged: () => Promise<void> | void;
  confirmFirst?: { title: string; description: string };
}) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  async function run() {
    if (confirmFirst && !(await confirm({ ...confirmFirst, confirmLabel: label, tone: "danger" }))) return;
    setBusy(true);
    try {
      await decideDisk(disk.id, decision);
      toast({ tone: "success", title: fill(toastText, { name: disk.display_name }) });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <button type="button" className={cn(secondary, "flex-1")} disabled={busy} onClick={() => void run()}>
      {label}
    </button>
  );
}

export function ApprovalFlow({
  disk,
  t,
  language,
  onChanged,
}: {
  disk: ExternalDisk;
  t: TranslationDictionary;
  language: Language;
  onChanged: () => Promise<void> | void;
}) {
  const text = t.disksV2.approval;
  const { toast } = useToast();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const warning = dataWarning(disk);
  const ok = nameConfirmed(disk, typed);

  async function approve() {
    if (!ok) return;
    setBusy(true);
    try {
      await decideDisk(disk.id, "approve", typed.trim());
      toast({ tone: "success", title: fill(text.approved, { name: disk.display_name }) });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 border-b border-solid border-border p-[18px]">
      {warning.hasData ? (
        <div className="flex gap-2.5 rounded-[10px] bg-warning/15 p-3 text-[13px] leading-[1.45] text-warning-text" role="alert">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <div className="flex flex-col gap-1.5">
            <b>{text.dataWarning}</b>
            {warning.partitions.length ? (
              <ul className="m-0 list-none p-0">
                {warning.partitions.map((part) => (
                  <li key={part.name} className="font-mono text-xs">
                    {[part.name, part.filesystem_type ?? text.noFs, part.label ? `"${part.label}"` : null, formatSize(part.size_gb, language)]
                      .filter(Boolean)
                      .join(" · ")}
                  </li>
                ))}
              </ul>
            ) : null}
            {warning.usedGb ? <span>{fill(text.used, { used: formatSize(warning.usedGb, language) })}</span> : null}
            <span>{text.dataWarningText}</span>
          </div>
        </div>
      ) : (
        <div className="rounded-[10px] bg-subtle p-3 text-[13px] leading-[1.45] text-muted">
          {warning.partitionsUnknown ? text.unknownText : text.blankText}
        </div>
      )}
      <label htmlFor={`confirm-name-${disk.id}`} className="text-[13px] text-muted">
        {text.typeName.split("{name}")[0]}
        <b className="font-mono text-fg">{disk.display_name}</b>
        {text.typeName.split("{name}")[1] ?? ""}
      </label>
      <input
        id={`confirm-name-${disk.id}`}
        aria-label={text.typeNameLabel}
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
        placeholder={disk.display_name}
        autoComplete="off"
        className="rounded-lg border border-solid border-border-strong bg-bg px-3 py-[9px] text-sm text-fg outline-none focus:border-accent"
      />
      <button
        type="button"
        disabled={!ok || busy}
        onClick={() => void approve()}
        className="rounded-lg border-0 bg-danger px-3.5 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {text.approve}
      </button>
      <div className="flex gap-2">
        <DecisionButton disk={disk} decision="ignore" label={text.ignore} toastText={text.ignoredToast} t={t} onChanged={onChanged} />
        <DecisionButton
          disk={disk}
          decision="reject"
          label={text.alwaysReject}
          toastText={text.rejectedToast}
          t={t}
          onChanged={onChanged}
          confirmFirst={{ title: text.rejectConfirmTitle, description: text.rejectConfirmText }}
        />
      </div>
    </div>
  );
}
