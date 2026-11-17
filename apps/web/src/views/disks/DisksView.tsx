import { useEffect, useMemo, useState } from "react";
import { useNow } from "../../ui/useNow";
import { AlertTriangle, ChevronRight, Loader2, Play, RefreshCw, Search, ShieldCheck, Unplug } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import {
  decideDisk,
  reactivateDisk,
  getExternalBackupReadiness,
  rescanDisks,
  type DatastoreHealth,
  type DiskAlert,
  type DiskDatastoreState,
  type DiskDecision,
  type ExternalBackupReadiness,
} from "../../api";
import { cn } from "../../components/ui/utils";
import { StatusDot } from "../../ui/StatusDot";
import type { Language, TranslationDictionary } from "../../i18n";
import { focusedDiskId } from "../../liveDisks";
import { ApiError } from "../../notices";
import { readinessTitle } from "../../readiness";
import { openRunModal } from "../../runs/openRun";
import type { ExternalBackupRun, ExternalDisk } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { PageTitle } from "../../ui/PageTitle";
import { useToast } from "../../ui/toast";
import { usageParts } from "../../units";
import { runPercent } from "../../runs/runModel";
import { fill, formatSize, formatWhen } from "../format";
import { DiskAvatar, PbsConfigNotice, VerifyNowButton } from "./DiskBits";
import { DiskHistoryList } from "./DiskSheetParts";
import { alertsFor, COLOR_CLASS, diskColor, diskSecondary, launchState } from "./diskProfile";
import { useDiskMenu } from "./diskMenu";
import { ActionMenuArea, ActionMenuButton } from "../../ui/ActionMenu";
import {
  DISK_TABS,
  dataWarning,
  diskActions,
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
  pbsConfigNote,
  type PbsConfigNote,
} from "./diskModel";

const TONE: Record<Tone, { pill: string; dot: string; icon: string; bar: string; note: string }> = {
  ok: { pill: "bg-success/15 text-success-text", dot: "bg-success", icon: "bg-success/15 text-success-text", bar: "bg-accent", note: "text-muted" },
  run: { pill: "bg-accent/15 text-accent-text", dot: "bg-accent", icon: "bg-accent/15 text-accent-text", bar: "bg-accent", note: "text-muted" },
  warn: { pill: "bg-warning/15 text-warning-text", dot: "bg-warning", icon: "bg-warning/15 text-warning-text", bar: "bg-warning", note: "text-warning-text" },
  err: { pill: "bg-danger/15 text-danger-text", dot: "bg-danger", icon: "bg-danger/15 text-danger-text", bar: "bg-danger", note: "text-danger-text" },
  off: { pill: "bg-subtle text-muted", dot: "bg-faint", icon: "bg-subtle text-muted", bar: "bg-accent", note: "text-muted" },
};


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
  /** Phase 6c: verdict of the last datastore check. */
  datastoreHealth?: DatastoreHealth | null;
  /** Phase 5f: guards (GET /disks/alerts). */
  alerts?: DiskAlert[];
}

/** A disk's state as « ● text » (docs/DESIGN.md); the name stays the export used everywhere. */
export function StatusPill({ status, label }: { status: DiskUiStatus; label: string }) {
  return (
    <StatusDot tone={STATUS_TONE[status]} pulse={status === "running"} data-status={status}>
      {label}
    </StatusDot>
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

export function DisksView({ disks, runs, language, t, onBackup, onEject, isStarting, isEjecting, onChanged, datastoreHealth, alerts }: DisksViewProps) {
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
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <PageTitle tip={text.info}>{text.title}</PageTitle>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void rescan()}
          disabled={rescanning}
          className="flex items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3.5 py-2 text-sm font-medium text-fg hover:brightness-110 disabled:opacity-60"
        >
          <RefreshCw className={cn("h-4 w-4", rescanning && "animate-spin")} />
          {text.rescan}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-b border-solid border-border">
        <div role="tablist" aria-label={text.tabsLabel} className="-mb-px flex min-w-0 flex-1 gap-5 overflow-x-auto">
          {DISK_TABS.filter((key) => key !== "hidden" || counts.hidden > 0 || tab === "hidden").map((key) => {
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
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-3.5">
          {shown.length === 0 ? (
            <div className="col-span-full rounded-lg border border-dashed border-border-strong px-6 py-10 text-center">
              <p className="m-0 text-sm font-medium">{query ? text.noMatch : text.empty}</p>
              {query ? null : <p className="m-0 mt-1.5 text-[13px] text-muted">{text.emptyHint}</p>}
            </div>
          ) : (
            shown.map((row) => (
              <DiskCard
                key={row.disk.id}
                configNote={pbsConfigNote(datastoreHealth?.disks[String(row.disk.id)])}
                row={row}
                alerts={alertsFor(alerts, row.disk.id)}
                runs={runs}
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
            configState={datastoreHealth?.disks[String(selected.disk.id)] ?? null}
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
            alerts={alertsFor(alerts, selected.disk.id)}
          />
        ) : (
          <aside className="rounded-lg border border-solid border-border bg-surface p-4 text-[13px] text-muted lg:sticky lg:top-4">
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
    // Phase 5f: used and total in one unit ("820 / 931 Gio").
    return fill(t.disksV2.usage, usageParts(disk.filesystem_used_gb, disk.filesystem_total_gb, language) ?? { usage: "—", pct: "—" });
  }
  if (row.status === "new") {
    const warning = dataWarning(disk);
    if (warning.hasData) return `${t.disksV2.approval.dataWarning}${warning.filesystem ? ` (${warning.filesystem})` : ""}`;
  }
  return "";
}

function noteText(row: DiskRow<ExternalDisk>, t: TranslationDictionary, language: Language): string {
  const reason = rejectReason(row.disk);
  if (row.status === "rejected" && reason) return rejectText(row.disk, reason, t, language);
  if (row.status === "running" && row.run) return `${t.disksV2.status.running} · ${Math.floor(runPercent(row.run as never))} %`;
  return t.disksV2.summary[row.status];
}

export function DiskCard({
  row,
  selected,
  onSelect,
  language,
  t,
  configNote,
  alerts = [],
  runs = [],
}: {
  configNote?: PbsConfigNote | null;
  alerts?: DiskAlert[];
  runs?: ExternalBackupRun[];
  row: DiskRow<ExternalDisk>;
  selected: boolean;
  onSelect: () => void;
  language: Language;
  t: TranslationDictionary;
}) {
  const { disk, status } = row;
  const tone = TONE[STATUS_TONE[status]];
  const usage = usagePercent(disk);
  const color = diskColor(disk.color);
  const usageBar = usage !== null && usage >= 90 ? "bg-danger" : usage !== null && usage >= 80 ? "bg-warning" : tone.bar;
  const tags = [
    status === "new" || status === "approved" || status === "running" ? t.disksV2.tagPlugged : null,
    disk.trusted ? t.disksV2.status.approved : null,
    disk.pbs_datastore_name ?? null,
  ].filter((tag): tag is string => Boolean(tag));
  const actions = useDiskMenu(disk, t, runs);
  const menuLabel = fill(t.menu.label, { name: disk.display_name });
  // B42: the card gives the state and ONE line; the panel gives the detail and the action.
  const line = cardLine(alerts, t);
  return (
    <ActionMenuArea
      actions={actions}
      label={menuLabel}
      data-disk={disk.id}
      className={cn(
        "relative flex flex-col rounded-lg border border-solid bg-surface text-fg outline-none focus-visible:ring-2 focus-visible:ring-accent",
        color ? cn("border-l-4", COLOR_CLASS[color].stripe) : "",
        selected ? "border-accent shadow-[0_0_0_3px_rgb(var(--pbo-accent)/0.18)]" : "border-border hover:border-border-strong",
      )}
    >
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className="flex flex-col gap-3 rounded-lg border-0 bg-transparent p-4 pr-11 text-left text-fg"
    >
      <div className="flex w-full items-start gap-3">
        <DiskAvatar disk={disk} t={t} />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-sm font-semibold">{disk.display_name}</span>
          <span className="truncate text-xs text-faint">{diskSecondary(disk)}</span>
        </div>
        <StatusPill status={status} label={t.disksV2.status[status]} />
      </div>
      <div className="flex w-full flex-col gap-1.5">
        <div className="flex gap-2 text-xs text-muted">
          <span className="min-w-0 flex-1 truncate">{capacityLine(row, language, t)}</span>
          <span className="flex-none">{formatSize(disk.capacity_gb, language)}</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-subtle">
          <div className={cn("h-full rounded-full", usageBar)} style={{ width: `${usage ?? 0}%` }} />
        </div>
      </div>
      {configNote ? (
        <span className={cn("text-[13px] font-medium", configNote === "missing_recoverable" ? "text-warning-text" : "text-danger-text")} data-config={configNote}>
          {t.datastoresV2.disk[configNote]}
        </span>
      ) : line ? (
        <span className={cn("text-[13px] font-medium", line.danger ? "text-danger-text" : "text-warning-text")} data-alert-line={line.code}>
          {line.text}
        </span>
      ) : (
        <span className={cn("text-[13px]", tone.note)}>{noteText(row, t, language)}</span>
      )}
      {tags.length ? (
        // Plain text (5i): badges are kept for exceptions.
        <p className="m-0 text-xs text-faint" data-tags>
          {tags.join(" · ")}
        </p>
      ) : null}
    </button>
    <ActionMenuButton actions={actions} label={menuLabel} className="absolute right-2 top-[52px]" />
    </ActionMenuArea>
  );
}

/** An alert's full sentence ("Rotation A is 91 % full"). */
function alertDetail(alert: DiskAlert, name: string, t: TranslationDictionary): string {
  const params = Object.fromEntries(Object.entries(alert.params).map(([key, value]) => [key, typeof value === "number" ? Math.round(value) : String(value ?? "")]));
  return fill((t.guards.alert as Record<string, string>)[alert.code] ?? alert.code, { name, ...params });
}

/** Readiness codes an alert of the panel already explains (B42: one message per alert). */
const COVERED_BY_ALERT: Record<string, string> = { smart_failed: "smart_danger", insufficient_space: "usage_critical", verify_recommended: "verify_recommended" };

function withoutCovered(readiness: ExternalBackupReadiness | undefined, alerts: readonly DiskAlert[]): ExternalBackupReadiness | undefined {
  if (!readiness || readiness.ready) return readiness;
  const codes = new Set<string>(alerts.map((alert) => alert.code));
  const reasons = readiness.reasons.filter((reason) => !codes.has(COVERED_BY_ALERT[reason.code] ?? reason.code));
  // Everything already explained above: no line under the button (it stays disabled).
  return reasons.length ? { ...readiness, reasons } : { ...readiness, reasons, ready: true };
}

/** B42: the one line a card shows for its alerts (the most important, "+N" for the others). */
export function cardLine(alerts: readonly DiskAlert[], t: TranslationDictionary): { code: string; text: string; danger: boolean } | null {
  const shown = alerts.filter((alert) => !alert.code.startsWith("pbs_"));
  if (!shown.length) return null;
  const [top] = shown;
  const badges = t.guards.badge as Record<string, string>;
  const params = Object.fromEntries(Object.entries(top.params).map(([key, value]) => [key, typeof value === "number" ? Math.round(value) : String(value ?? "")]));
  const more = shown.length > 1 ? ` · ${fill(t.disksV2.moreAlerts, { n: shown.length - 1 })}` : "";
  return { code: top.code, text: `${fill(badges[top.code] ?? top.code, params)}${more}`, danger: top.level === "danger" };
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
/** Discreet text button (Phase 5h.1: "Run detection again" next to "Reactivate"). */
const quiet = "flex w-fit items-center gap-1.5 self-start rounded-md border-0 bg-transparent px-1 py-1 text-[13px] font-medium text-muted hover:text-fg disabled:opacity-50";
const secondary = "flex items-center justify-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3 py-2 text-[13px] font-medium text-fg hover:brightness-110 disabled:cursor-not-allowed disabled:text-muted disabled:hover:brightness-100";

export function DiskDetail({
  configState,
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
  alerts = [],
}: {
  configState?: DiskDatastoreState | null;
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
  alerts?: DiskAlert[];
}) {
  const { disk, status } = row;
  const text = t.disksV2;
  // B42: each alert once, here, with its detail and its action (the card only gives one line).
  const detailed = alerts.filter((alert) => !alert.code.startsWith("pbs_") && alert.code !== "verify_recommended");
  const now = useNow();
  const when = (iso: string | null) => formatWhen(iso, language, now, t.dashboardV2);
  const actions = diskActions(disk, status);
  const readiness = useReadiness(disk, status, runs);
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
    <aside aria-label={text.selected} className="flex min-w-0 flex-col rounded-lg border border-solid border-border bg-surface lg:sticky lg:top-4">
      <div className="flex flex-col gap-1 border-b border-solid border-border p-4">
        <span className="text-xs text-faint">{text.selected}</span>
        <span className="text-[17px] font-semibold">{disk.display_name}</span>
        <span className="text-[13px] text-muted">{text.summary[status]}</span>
        {status !== "new" ? (
          <Link to={`/disks/${disk.id}`} className="mt-1 flex w-fit items-center gap-1 text-[13px] font-medium text-accent-text no-underline hover:underline">
            {t.diskSheet.open}
            <ChevronRight className="h-4 w-4" />
          </Link>
        ) : null}
      </div>

      {disk.verify_recommended ? (
        <div className="border-0 border-b border-solid border-border p-4" data-verify-recommended>
          <div className="flex flex-col gap-2.5 rounded-md bg-warning/15 p-3 text-[13px] leading-[1.45] text-warning-text">
            <span className="flex gap-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 flex-none" />
              <span className="flex flex-col gap-0.5">
                <b>{t.diskSheet.verify.title}</b>
                <span>{disk.connected ? t.diskSheet.verify.short : t.diskSheet.verify.needsPlug}</span>
              </span>
            </span>
            {disk.connected ? <VerifyNowButton disk={disk} t={t} onChanged={onChanged} /> : null}
          </div>
        </div>
      ) : null}

      {pbsConfigNote(configState) ? (
        <div className="border-0 border-b border-solid border-border p-4" data-config-detail={pbsConfigNote(configState)}>
          <PbsConfigNotice note={pbsConfigNote(configState) as PbsConfigNote} datastore={configState?.datastore ?? ""} serial={disk.serial_number} t={t} />
        </div>
      ) : null}

      {detailed.length ? (
        <ul className="m-0 flex list-none flex-col gap-2 border-0 border-b border-solid border-border p-4" data-alert-details={detailed.map((alert) => alert.code).join(",")}>
          {detailed.map((alert) => (
            <li
              key={alert.code}
              className={cn(
                "flex flex-col gap-2 rounded-md p-3 text-[13px] leading-[1.45]",
                alert.level === "danger" ? "bg-danger/15 text-danger-text" : "bg-warning/15 text-warning-text",
              )}
            >
              <span>{alertDetail(alert, disk.display_name, t)}</span>
              <Link to={`/disks/${disk.id}`} className="w-fit text-[13px] font-medium text-fg underline-offset-2 hover:underline">
                {(t.guards.action as Record<string, string>)[alert.code] ?? t.diskSheet.open}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {status === "new" ? <ApprovalFlow disk={disk} t={t} language={language} onChanged={onChanged} /> : null}

      {status === "rejected" && reason ? (
        <div className="border-b border-solid border-border p-4">
          <div className="rounded-md bg-danger/15 p-3 text-[13px] leading-[1.45] text-danger-text">
            {rejectText(disk, reason, t, language)}
          </div>
        </div>
      ) : null}

      {actions.length ? (
        <div className="flex flex-col gap-2.5 border-b border-solid border-border p-4">
          {actions.map((action) => {
            switch (action) {
              case "backup": {
                // A failing SMART can be overridden in the launch dialog (explicit confirmation).
                const blocked = !readiness || !launchState(readiness, true).canStart;
                // B42: what the alerts above already explain is not repeated under the button.
                const title = readinessTitle(withoutCovered(readiness, alerts), t.readiness as Record<string, string>, language);
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
                    {row.run ? fill(text.action.followPct, { pct: Math.floor(runPercent(row.run as never)) }) : text.action.follow}
                  </button>
                );
              case "eject":
                return (
                  <button
                    key={action}
                    type="button"
                    className={secondary}
                    disabled={status === "running" || ejecting}
                    title={status === "running" ? t.menu.disk.ejectRunningHint : undefined}
                    onClick={() => onEject(disk)}
                  >
                    <Unplug className="h-4 w-4" />
                    {status === "running" ? text.action.ejectAfter : text.action.eject}
                  </button>
                );
              case "replug":
                // Phase 5h.1: a hint, not a button ("Reactivate" above does it without unplugging).
                return (
                  <p key={action} className="m-0 flex items-start gap-2 text-[13px] leading-[1.45] text-muted" data-replug-hint>
                    <Unplug className="mt-0.5 h-4 w-4 flex-none text-faint" />
                    {text.action.replugHint}
                  </p>
                );
              case "rescan":
                return (
                  <button
                    key={action}
                    type="button"
                    className={status === "ejected" ? quiet : secondary}
                    disabled={rescanning}
                    onClick={onRescan}
                    data-rescan
                  >
                    <RefreshCw className={cn("h-4 w-4", rescanning && "animate-spin")} />
                    {text.action.rescan}
                  </button>
                );
              case "unreject":
                return <DecisionButton key={action} disk={disk} decision="clear" label={text.action.unreject} toastText={text.approval.unrejectedToast} t={t} onChanged={onChanged} />;
              case "hide":
                return <DecisionButton key={action} disk={disk} decision="hide" label={text.action.hide} toastText={text.approval.hiddenToast} t={t} onChanged={onChanged} />;
              case "unhide":
                return <DecisionButton key={action} disk={disk} decision="clear" label={text.action.unhide} toastText={text.approval.unhiddenToast} t={t} onChanged={onChanged} />;
              case "reactivate":
                return <ReactivateButton key={action} disk={disk} t={t} onChanged={onChanged} />;
              case "approveAnyway":
                return <ApproveAnyway key={action} disk={disk} t={t} language={language} onChanged={onChanged} />;
            }
          })}
        </div>
      ) : null}

      <div className="px-4 pb-1.5 pt-3.5 text-[13px] font-semibold">{text.history}</div>
      <div className="px-[14px]">
        <DiskHistoryList disk={disk} runs={runs} language={language} t={t} pageSize={5} />
      </div>

      <details className="mt-2.5 border-t border-solid border-border">
        <summary className="cursor-pointer px-4 py-3.5 text-[13px] font-medium text-muted">{text.technical}</summary>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 px-4 pb-4 text-xs">
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

/** B49: "Reactivate" on an ejected disk (the host agent re-enumerates its USB device). */
function ReactivateButton({ disk, t, onChanged }: { disk: ExternalDisk; t: TranslationDictionary; onChanged: () => Promise<void> | void }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      await reactivateDisk(disk.id);
      toast({ tone: "success", title: fill(t.disksV2.action.reactivated, { name: disk.display_name }) });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <button type="button" className={primary} disabled={busy} onClick={() => void run()} data-reactivate>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
      {busy ? t.disksV2.action.reactivating : t.disksV2.action.reactivate}
    </button>
  );
}

/** "Too small: 16 GiB, a PBS datastore needs at least 32 GiB." (size next to the minimum). */
function rejectText(disk: ExternalDisk, reason: string, t: TranslationDictionary, language: Language): string {
  const template = t.disksV2.rejectReason[reason as keyof typeof t.disksV2.rejectReason] ?? "";
  return fill(template, { min: disk.min_size_gb ?? 32, size: formatSize(disk.capacity_gb, language) });
}

/** Phase 5g: a disk under the minimum size, approved after reading the risk and typing its name. */
function ApproveAnyway({
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
  const ok = nameConfirmed(disk, typed);
  async function approve() {
    if (!ok) return;
    setBusy(true);
    try {
      await decideDisk(disk.id, "approve_anyway", typed.trim());
      toast({ tone: "success", title: fill(text.anywayApproved, { name: disk.display_name }) });
      await onChanged();
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-2.5 rounded-md bg-warning/15 p-3 text-[13px] leading-[1.45] text-warning-text" data-approve-anyway>
      <b>{text.anywayTitle}</b>
      <span>{fill(text.anywayRisk, { size: formatSize(disk.capacity_gb, language), min: disk.min_size_gb ?? 32 })}</span>
      <label className="flex flex-col gap-1 text-fg">
        <span className="text-xs text-muted">{fill(text.typeName, { name: disk.display_name })}</span>
        <input
          className="rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent"
          aria-label={text.typeNameLabel}
          autoComplete="off"
          placeholder={disk.display_name}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
      </label>
      <button type="button" className={secondary} disabled={!ok || busy} onClick={() => void approve()}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <AlertTriangle className="h-4 w-4" />}
        {t.disksV2.action.approveAnyway}
      </button>
    </div>
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
    <div className="flex flex-col gap-3 border-b border-solid border-border p-4">
      {warning.hasData ? (
        <div className="flex gap-2.5 rounded-md bg-warning/15 p-3 text-[13px] leading-[1.45] text-warning-text" role="alert">
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
        <div className="rounded-md bg-subtle p-3 text-[13px] leading-[1.45] text-muted">
          {warning.partitionsUnknown ? text.unknownText : text.blankText}
        </div>
      )}
      <label htmlFor={`confirm-name-${disk.id}`} className="text-[13px] text-muted">
        {text.typeName.split("{name}")[0]}
        <b className="font-semibold text-fg">{disk.display_name}</b>
        {text.typeName.split("{name}")[1] ?? ""}
      </label>
      <input
        id={`confirm-name-${disk.id}`}
        aria-label={text.typeNameLabel}
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
        placeholder={disk.display_name}
        autoComplete="off"
        className="rounded-lg border border-solid border-border-strong bg-bg px-3 py-2 text-sm text-fg outline-none focus:border-accent"
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
