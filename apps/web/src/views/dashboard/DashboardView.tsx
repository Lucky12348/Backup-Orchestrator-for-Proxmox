import { useState } from "react";
import { CalendarDays, HardDrive, Loader2, Play } from "lucide-react";
import { Link } from "react-router-dom";

import { cancelScheduledBackupRun, confirmScheduledBackupRun, type DiskAlert } from "../../api";
import { useToast } from "../../ui/toast";
import { ChooseDatastoreDialog } from "../datastores/ChooseDatastoreDialog";

import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { openRunModal } from "../../runs/openRun";
import type { AgentStatus, ExternalBackupRun, ExternalDisk, Overview, ScheduledBackupEvent, ScheduledBackupRun } from "../../types";
import { InfoTip } from "../../ui/InfoTip";
import { runKind } from "../disks/diskModel";
import { alertValues } from "../disks/diskProfile";
import { formatUsage } from "../../units";
import { fill, formatDay, formatSize, formatTime, formatWhen } from "../format";
import {
  currentOrLastRun,
  integrations,
  lastSuccessAt,
  nextRotation,
  protectionSummary,
  recentRuns,
  rotationDisks,
  todoItems,
} from "./dashboardModel";

export interface DashboardViewProps {
  overview: Overview;
  disks: ExternalDisk[];
  runs: ExternalBackupRun[];
  events: ScheduledBackupEvent[];
  scheduledRuns: ScheduledBackupRun[];
  agentStatus: AgentStatus;
  pveConnected: boolean;
  pbsConnected: boolean;
  agentConfigured: boolean;
  language: Language;
  t: TranslationDictionary;
  now?: Date;
  /** Reload the app data after confirming / cancelling a planned run. */
  onChanged?: () => Promise<void> | void;
  /** Source datastore deleted on PBS (Phase 6c): "Choose another datastore". */
  missingDatastore?: string | null;
  /** Phase 5f: disk guards (GET /disks/alerts). */
  alerts?: DiskAlert[];
}

const RING = 2 * Math.PI * 56;

function Ring({ percent, tone, label }: { percent: number; tone: "ok" | "warn" | "off"; label: string }) {
  const stroke = tone === "ok" ? "rgb(var(--pbo-success))" : tone === "warn" ? "rgb(var(--pbo-warning))" : "rgb(var(--pbo-border-strong))";
  const filled = (Math.max(0, Math.min(100, percent)) / 100) * RING;
  return (
    <div className="relative h-[132px] w-[132px] flex-none">
      <svg width="132" height="132" viewBox="0 0 132 132" aria-hidden="true">
        <circle cx="66" cy="66" r="56" fill="none" stroke="rgb(var(--pbo-subtle))" strokeWidth="12" />
        {filled > 0 ? (
          <circle
            cx="66"
            cy="66"
            r="56"
            fill="none"
            stroke={stroke}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={`${filled} ${RING}`}
            transform="rotate(-90 66 66)"
          />
        ) : null}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[30px] font-semibold tracking-[-0.03em]">{percent} %</span>
        <span className="text-xs text-faint">{label}</span>
      </div>
    </div>
  );
}

const card = "rounded-2xl border border-solid border-border bg-surface";
const rowLink = "flex items-center text-fg no-underline hover:bg-subtle";

/** "To confirm: Weekly rotation" with Confirm / Cancel (planned run, manual confirmation). */
function ConfirmRow({ label, runId, t, onChanged }: { label: string; runId: number; t: TranslationDictionary; onChanged?: () => Promise<void> | void }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  async function act(confirmIt: boolean) {
    setBusy(true);
    try {
      if (confirmIt) await confirmScheduledBackupRun(runId);
      else await cancelScheduledBackupRun(runId);
      toast({ tone: confirmIt ? "success" : "info", title: confirmIt ? t.planningV2.confirmDone : t.planningV2.cancelDone });
      await onChanged?.();
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-3 px-[22px] py-3" data-todo="to_confirm">
      <span className="h-2 w-2 flex-none rounded-full bg-warning" />
      <span className="min-w-0 flex-1 text-sm">{label}</span>
      <button type="button" disabled={busy} onClick={() => void act(true)} className="rounded-[7px] border-0 bg-accent px-2.5 py-1 text-xs font-medium text-white hover:brightness-110 disabled:opacity-60">
        {t.dashboardV2.todoConfirm}
      </button>
      <button type="button" disabled={busy} onClick={() => void act(false)} className="rounded-[7px] border border-solid border-border bg-subtle px-2.5 py-1 text-xs font-medium text-fg hover:brightness-110 disabled:opacity-60">
        {t.dashboardV2.todoCancel}
      </button>
    </div>
  );
}

export function DashboardView(props: DashboardViewProps) {
  const { overview, disks, runs, language, t } = props;
  const text = t.dashboardV2;
  const now = props.now ?? new Date();
  const when = (iso: string | null) => formatWhen(iso, language, now, text);
  const protection = protectionSummary(overview);
  const current = currentOrLastRun(runs);
  const lastSuccess = lastSuccessAt(runs);
  const links = integrations({ pve: props.pveConnected, pbs: props.pbsConnected, agent: props.agentStatus.status === "connected" });
  const rotation = nextRotation(props.events, disks, now);
  const todo = todoItems({
    disks,
    runs,
    scheduledRuns: props.scheduledRuns,
    agentConnected: props.agentStatus.status === "connected",
    agentConfigured: props.agentConfigured,
    now,
    missingDatastore: props.missingDatastore,
    alerts: props.alerts,
  });
  const [choosing, setChoosing] = useState(false);
  const rotationList = rotationDisks(disks, runs);
  const lastRuns = recentRuns(runs);
  const infrastructure = overview.infrastructure_ignored ?? 0;

  const headline = protection.state === "protected" ? text.protected : protection.state === "at_risk" ? text.atRisk : text.empty;
  const sentence =
    protection.state === "protected"
      ? fill(text.protectedText, { n: protection.total })
      : protection.state === "at_risk"
        ? fill(text.atRiskText, { n: protection.atRisk, total: protection.total })
        : text.emptyText;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{text.title}</h1>
        <InfoTip text={text.info} label={t.shell.about} />
        <span className="flex-1" />
        <Link to="/planning" className="flex items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3.5 py-[9px] text-sm font-medium text-fg no-underline hover:brightness-110">
          <CalendarDays className="h-4 w-4" />
          {text.plan}
        </Link>
        <Link to="/disks?tab=approved" className="flex items-center gap-2 rounded-lg bg-accent px-3.5 py-[9px] text-sm font-medium text-white no-underline hover:brightness-110">
          <Play className="h-4 w-4" />
          {text.startBackup}
        </Link>
      </div>

      <div className="flex flex-wrap items-stretch gap-5">
        <section aria-label={text.protectionLabel} className={cn(card, "flex min-w-0 flex-[2_1_520px] flex-col")} data-protection={protection.state}>
          <div className="flex flex-wrap items-center gap-7 p-7">
            <Ring percent={protection.percent} tone={protection.state === "protected" ? "ok" : protection.state === "at_risk" ? "warn" : "off"} label={text.coverage} />
            <div className="flex flex-[1_1_260px] flex-col gap-2.5">
              <span className={cn("text-2xl font-semibold tracking-[-0.02em]", protection.state === "at_risk" && "text-warning-text")}>{headline}</span>
              <span className="text-[15px] leading-normal text-muted">{sentence}</span>
              <span className="text-[13px] text-faint">
                {lastSuccess ? fill(text.lastSuccess, { when: when(lastSuccess) }) : text.noSuccess}
                {protection.ignored > 0 ? (
                  <>
                    {" · "}
                    {protection.ignored === 1 ? text.ignoredOne : fill(text.ignored, { n: protection.ignored })}
                    {infrastructure > 0 ? ` (${fill(text.infrastructure, { n: infrastructure })})` : null}
                    {" · "}
                    <Link to="/assets" className="text-accent-text no-underline hover:underline">
                      {text.reinclude}
                    </Link>
                  </>
                ) : null}
              </span>
            </div>
          </div>
          {current ? (
            <button
              type="button"
              onClick={() => openRunModal(current.run.id)}
              className={cn(rowLink, "flex-wrap gap-3.5 border-0 border-t border-solid border-border bg-transparent px-7 py-4 text-left")}
            >
              {current.active ? (
                <>
                  <Loader2 className="h-4 w-4 flex-none animate-spin text-accent-text" />
                  <span className="text-sm font-medium">
                    {fill(runKind(current.run) === "eject" ? text.ejecting : text.running, { disk: current.run.disk_name })}
                  </span>
                  <span className="block h-1.5 flex-[1_1_120px] overflow-hidden rounded-full bg-subtle">
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${current.run.progress_percent ?? 0}%` }} />
                  </span>
                  <span className="font-mono text-[13px] text-muted">{Math.round(current.run.progress_percent ?? 0)} %</span>
                  <span className="text-[13px] font-medium text-accent-text">{text.follow}</span>
                </>
              ) : (
                <>
                  <span
                    className={cn(
                      "h-2 w-2 flex-none rounded-full",
                      current.run.status === "success" ? "bg-success" : "bg-danger",
                    )}
                  />
                  <span className="flex-1 text-sm">
                    {fill(current.run.status === "success" ? text.lastRunOk : text.lastRunFailed, { disk: current.run.disk_name })}
                  </span>
                  <span className="text-xs text-faint">{when(current.run.finished_at ?? current.run.started_at)}</span>
                  <span className="text-[13px] font-medium text-accent-text">{text.open}</span>
                </>
              )}
            </button>
          ) : null}
          <div className="flex flex-wrap items-center gap-[18px] border-t border-solid border-border px-7 py-3.5 text-[13px] text-muted">
            {links.items.map((item) => (
              <span key={item.key} className="flex items-center gap-2" data-integration={item.key} data-ok={item.ok}>
                <span className={cn("h-[7px] w-[7px] rounded-full", item.ok ? "bg-success" : "bg-danger")} />
                {text.integration[item.key]}
              </span>
            ))}
            <span className="flex-1" />
            {links.allOk ? (
              <span className="text-faint">{text.allConnected}</span>
            ) : (
              <Link to="/integrations" className="text-accent-text no-underline hover:underline">
                {text.someDown}
              </Link>
            )}
          </div>
        </section>

        <div className="flex min-w-0 flex-[1_1_300px] flex-col gap-5">
          <section className={cn(card, "flex flex-col gap-3.5 p-[22px]")}>
            <span className="text-[13px] text-faint">{text.nextRotation}</span>
            {rotation ? (
              <>
                <div className="flex flex-col gap-1">
                  <span className="text-2xl font-semibold tracking-[-0.02em]">{formatDay(rotation.at, language)}</span>
                  <span className="text-sm text-muted">
                    {formatTime(rotation.at, language)} · {rotation.title}
                  </span>
                </div>
                <Link
                  to={rotation.diskId ? `/disks?disk=${rotation.diskId}` : "/disks"}
                  className="flex items-center gap-3 rounded-[10px] bg-subtle p-3 text-fg no-underline"
                >
                  <HardDrive className="h-[18px] w-[18px] flex-none text-muted" />
                  <div className="flex flex-1 flex-col">
                    <span className="text-sm font-medium">{rotation.diskLabel}</span>
                    <span className={cn("text-xs", rotation.diskPlugged ? "text-success-text" : "text-warning-text")}>
                      {rotation.diskPlugged ? text.diskPlugged : fill(text.diskToPlug, { day: formatDay(rotation.at, language) })}
                    </span>
                  </div>
                </Link>
              </>
            ) : (
              <div className="flex flex-col gap-2">
                <span className="text-lg font-semibold">{text.noRotation}</span>
                <Link to="/planning" className="text-[13px] text-accent-text no-underline hover:underline">
                  {text.planRotation}
                </Link>
              </div>
            )}
          </section>

          <section className={cn(card, "flex flex-1 flex-col")} aria-label={text.todo}>
            <div className="flex items-center gap-2 px-[22px] pb-2 pt-[18px]">
              <span className="flex-1 text-[15px] font-semibold">{text.todo}</span>
              {todo.length ? (
                <span className="rounded-full bg-warning/15 px-2 py-px text-xs font-semibold text-warning-text">{todo.length}</span>
              ) : null}
            </div>
            {todo.length === 0 ? (
              <span className="px-[22px] pb-4 pt-1 text-[13px] text-faint">{text.todoEmpty}</span>
            ) : (
              todo.map((item, index) =>
                item.kind === "datastore_missing" ? (
                  <div key="datastore" className="flex flex-wrap items-center gap-3 px-[22px] py-3" data-todo="datastore_missing">
                    <span className="h-2 w-2 flex-none rounded-full bg-warning" />
                    <span className="min-w-0 flex-1 text-sm">{fill(text.todoItem.datastore_missing, { name: item.label })}</span>
                    <button type="button" onClick={() => setChoosing(true)} className="rounded-[7px] border-0 bg-accent px-2.5 py-1 text-xs font-medium text-white hover:brightness-110">
                      {t.datastoresV2.chooseAction}
                    </button>
                  </div>
                ) : item.kind === "disk_alert" && item.alert ? (
                  <Link key={`alert-${item.alert.disk_id}-${item.alert.code}`} to={item.href} className={cn(rowLink, "gap-3 px-[22px] py-3")} data-todo={`alert:${item.alert.code}`}>
                    <span className={cn("h-2 w-2 flex-none rounded-full", item.tone === "err" ? "bg-danger" : "bg-warning")} />
                    <span className="flex-1 text-sm">
                      {fill((t.guards.alert as Record<string, string>)[item.alert.code] ?? item.alert.code, alertValues(item.alert, (iso) => when(iso)))}
                    </span>
                    <span className="text-[13px] text-accent-text">{(t.guards.action as Record<string, string>)[item.alert.code] ?? text.open}</span>
                  </Link>
                ) : item.kind === "to_confirm" && item.runId ? (
                  <ConfirmRow key={`${item.kind}-${item.runId}`} label={fill(text.todoItem.to_confirm, { name: item.label })} runId={item.runId} t={t} onChanged={props.onChanged} />
                ) : (
                  <Link key={`${item.kind}-${index}`} to={item.href} className={cn(rowLink, "gap-3 px-[22px] py-3")} data-todo={item.kind}>
                    <span className={cn("h-2 w-2 flex-none rounded-full", item.tone === "err" ? "bg-danger" : "bg-warning")} />
                    <span className="flex-1 text-sm">{fill((text.todoItem as Record<string, string>)[item.kind] ?? "", { name: item.label })}</span>
                    <span className="text-[13px] text-accent-text">{(text.todoAction as Record<string, string>)[item.kind]}</span>
                  </Link>
                ),
              )
            )}
          </section>
        </div>
      </div>

      <ChooseDatastoreDialog
        open={choosing}
        missing={props.missingDatastore ?? null}
        onClose={() => setChoosing(false)}
        onChanged={() => props.onChanged?.()}
        language={language}
        t={t}
      />

      <div className="flex flex-wrap gap-5">
        <section className={cn(card, "flex min-w-0 flex-[1_1_400px] flex-col pb-2")}>
          <div className="flex items-center px-[22px] pb-2.5 pt-[18px]">
            <h2 className="m-0 flex-1 text-[15px] font-semibold">{text.rotationDisks}</h2>
            <Link to="/disks" className="text-[13px] text-accent-text no-underline hover:underline">
              {text.allDisks}
            </Link>
          </div>
          {rotationList.length === 0 ? (
            <span className="px-[22px] pb-3 text-[13px] text-faint">{text.noRotationDisks}</span>
          ) : (
            rotationList.map((disk) => (
              <Link key={disk.id} to={`/disks?disk=${disk.id}`} className={cn(rowLink, "gap-3.5 px-[22px] py-3.5")}>
                <div className="flex w-[140px] flex-none flex-col gap-0.5">
                  <span className="truncate text-sm font-medium">{disk.name}</span>
                  <span
                    className={cn(
                      "truncate text-xs",
                      disk.status === "running" ? "text-accent-text" : disk.status === "approved" ? "text-success-text" : "text-faint",
                    )}
                  >
                    {disk.status === "running"
                      ? fill(text.diskRunning, { pct: Math.round(disk.percent ?? 0) })
                      : t.disksV2.status[disk.status]}
                  </span>
                </div>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="h-1.5 overflow-hidden rounded-full bg-subtle">
                    <div className="h-full rounded-full bg-muted opacity-55" style={{ width: `${disk.usage ?? 0}%` }} />
                  </div>
                  <span className="text-xs text-faint">
                    {/* Phase 5f: used and total in one unit, like the disk cards. */}
                    {disk.usedGb != null
                      ? fill(t.disksV2.usage, { usage: formatUsage(disk.usedGb, disk.totalGb, language), pct: disk.usage ?? 0 })
                      : formatSize(disk.totalGb, language)}
                  </span>
                </div>
                <span className="w-[92px] text-right text-xs text-faint">{when(disk.lastBackupAt)}</span>
              </Link>
            ))
          )}
        </section>

        <section className={cn(card, "flex min-w-0 flex-[1_1_400px] flex-col pb-2")}>
          <div className="flex items-center px-[22px] pb-2.5 pt-[18px]">
            <h2 className="m-0 flex-1 text-[15px] font-semibold">{text.recentRuns}</h2>
            <Link to="/activity" className="text-[13px] text-accent-text no-underline hover:underline">
              {text.history}
            </Link>
          </div>
          {lastRuns.length === 0 ? (
            <span className="px-[22px] pb-3 text-[13px] text-faint">{text.noRuns}</span>
          ) : (
            lastRuns.map(({ run, tone }) => (
              <button
                key={run.id}
                type="button"
                onClick={() => openRunModal(run.id)}
                className={cn(rowLink, "gap-3 border-0 bg-transparent px-[22px] py-[11px] text-left")}
                data-run={run.id}
                data-tone={tone}
              >
                <span
                  className={cn(
                    "flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full text-[10px] font-bold",
                    tone === "ok" ? "bg-success/15 text-success-text" : tone === "err" ? "bg-danger/15 text-danger-text" : "bg-accent/15 text-accent-text",
                  )}
                  aria-hidden="true"
                >
                  {tone === "ok" ? "✓" : tone === "err" ? "✕" : "•"}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm">
                  {fill(runKind(run) === "eject" ? text.runEject : text.runBackup, { disk: run.disk_name })}
                </span>
                <span className="text-xs text-faint">{tone === "run" ? text.inProgress : when(run.finished_at ?? run.started_at)}</span>
              </button>
            ))
          )}
        </section>
      </div>
    </div>
  );
}
