import { useEffect, useState } from "react";
import { RepairButton } from "../views/disks/DiskBits";
import { AlertTriangle, Check, Download, Loader2, Minimize2, X } from "lucide-react";

import { cancelExternalBackupRun } from "../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { cn } from "../components/ui/utils";
import type { Language, TranslationDictionary } from "../i18n";
import { useToast } from "../ui/toast";
import { useRunMonitor } from "./RunMonitor";
import {
  clockTime,
  copySummary,
  entryText,
  filterEntries,
  formatBytes,
  formatDuration,
  formatRate,
  isEjectRun,
  isRunning,
  journalText,
  LEVEL_LABEL,
  resourceRows,
  RUN_STEPS,
  runSteps,
  type RunStepKey,
  runStats,
  stepIndex,
  stepStates,
  type JournalEntry,
  type LogFilter,
  type ResourceRow,
  type RunLike,
  type StepState,
} from "./runModel";

function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => (key in values ? String(values[key]) : whole));
}

/** Live run modal (docs/design/mockups/live-run-modal.html), fed by SSE. */
export function LiveRunModal({ t, language }: { t: TranslationDictionary; language: Language }) {
  const monitor = useRunMonitor();
  const run = monitor.activeRun;
  const open = monitor.state.open && run !== null;
  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? (run && isRunning(run) ? monitor.minimize() : monitor.close()) : undefined)}>
      {run ? (
        <DialogContent className="max-w-[1040px]" aria-describedby="run-subtitle">
          <RunView run={run} t={t} language={language} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

export function RunView({ run, t, language }: { run: RunLike; t: TranslationDictionary; language: Language }) {
  const monitor = useRunMonitor();
  const { toast } = useToast();
  const text = t.runModal;
  const lang = language === "fr" ? "fr" : "en";
  const entries: JournalEntry[] = (monitor.journal?.entries ?? []) as JournalEntry[];
  const [now, setNow] = useState(() => Date.now());
  const [filter, setFilter] = useState<LogFilter>("all");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const running = isRunning(run);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  const eject = isEjectRun(run, entries);
  const states = stepStates(run, entries);
  const stats = runStats(run, now);
  const resources = resourceRows(run, entries);
  const summary = monitor.journal?.summary ?? null;
  const source = monitor.journal?.source_datastore ?? run.source_datastore;
  const target = monitor.journal?.target_datastore ?? run.target_datastore;
  const diskName = monitor.diskName(run.disk_id) ?? run.disk_name ?? `#${run.disk_id}`;
  const phase = stepIndex(run.current_step);

  const title = eject
    ? run.status === "failed"
      ? text.title.ejectFailed
      : running
        ? text.title.ejectRunning
        : text.title.ejectSuccess
    : run.status === "failed"
      ? run.current_step === "disk_pulled"
        ? text.title.interrupted
        : run.repair
          ? text.title.repairFailed
          : run.verify_only
            ? text.title.verifyFailed
            : text.title.failed
      : run.repair
        ? run.status === "success"
          ? text.title.repairOk
          : text.title.repair
        : run.verify_only
        ? run.status === "success"
          ? text.title.verifyOk
          : text.title.verify
        : run.status === "success"
          ? states[RUN_STEPS.indexOf("eject")] === "done"
            ? text.title.success
            : text.title.successKeep
          : phase === RUN_STEPS.indexOf("eject")
            ? text.title.ejecting
            : phase === RUN_STEPS.indexOf("verify")
              ? text.title.verify
              : phase === RUN_STEPS.indexOf("cleanup")
                ? text.title.cleanup
                : text.title.running;

  const headerTone =
    run.status === "failed" ? "bg-danger/15 text-danger-text" : run.status === "success" ? "bg-success/15 text-success-text" : "bg-accent text-accent-fg";
  const barColor = run.status === "failed" ? "bg-danger" : run.status === "success" ? "bg-success" : "bg-accent";

  const statsLine = [
    summary
      ? copySummary(summary, text, lang)
      : stats.transferredBytes !== null
        ? fill(text.transferred, { bytes: formatBytes(stats.transferredBytes, lang) })
        : null,
    formatRate(stats.rate, lang),
    running && stats.remainingSeconds !== null ? fill(text.remaining, { duration: formatDuration(stats.remainingSeconds, lang) ?? "" }) : null,
    !running ? formatDuration(stats.elapsedSeconds, lang) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const visible = filterEntries(entries, filter);

  function download() {
    const blob = new Blob([journalText(entries, t.runLog as Record<string, string>, monitor.journal?.timezone, lang)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `pbo-run-${run.id}.log`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function doCancel() {
    setCancelling(true);
    try {
      await cancelExternalBackupRun(run.id);
      toast({ tone: "warning", title: text.cancelRequested });
      monitor.refresh();
    } catch (error) {
      toast({ tone: "danger", title: text.cancelFailed, description: error instanceof Error ? error.message : undefined });
    } finally {
      setCancelling(false);
      setConfirmCancel(false);
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-start gap-3.5 px-[22px] pb-4 pt-5">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-[10px]", headerTone)}>
          {running ? <Loader2 className="h-5 w-5 animate-spin" /> : run.status === "success" ? <Check className="h-5 w-5" strokeWidth={2.4} /> : <AlertTriangle className="h-5 w-5" />}
        </div>
        <div className="flex min-w-[240px] flex-1 flex-col gap-1">
          <DialogTitle className="m-0 text-lg font-semibold tracking-[-0.01em]">{title}</DialogTitle>
          <DialogDescription id="run-subtitle" className="m-0 text-[13px] text-muted">
            {eject ? text.kind.eject : run.repair ? text.kind.repair : run.verify_only ? text.kind.verify : text.kind.backup} · {fill(text.subtitle, { time: clockTime(run.started_at.endsWith("Z") ? run.started_at : `${run.started_at}Z`) })} · {diskName}
            {target ? (
              <>
                {" "}
                <span className="font-mono text-faint">
                  {source ? `${source} → ` : ""}
                  {target}
                </span>
              </>
            ) : null}
          </DialogDescription>
        </div>
        {running ? (
          <button
            type="button"
            onClick={monitor.minimize}
            className="flex items-center gap-2 rounded-lg border border-border px-3 py-[7px] text-[13px] font-medium text-muted hover:bg-subtle"
          >
            <Minimize2 className="h-[15px] w-[15px]" />
            {text.minimize}
          </button>
        ) : null}
        <button
          type="button"
          aria-label={text.close}
          onClick={running ? monitor.minimize : monitor.close}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-faint hover:bg-subtle hover:text-fg"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex flex-col gap-2.5 px-[22px] pb-[18px]">
        <div className="flex flex-wrap items-baseline gap-3.5">
          <span className="text-[40px] font-semibold tracking-[-0.03em] tabular-nums">{Math.floor(stats.percent)} %</span>
          <span className="font-mono text-[13px] text-muted">{statsLine}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-subtle">
          <div className={cn("h-full rounded-full transition-[width] duration-500", barColor)} style={{ width: `${stats.percent.toFixed(1)}%` }} />
        </div>
      </div>

      <StepList states={states} steps={runSteps(run)} t={t} />

      <div className="flex min-h-0 flex-1 flex-wrap overflow-auto">
        <ResourceList rows={resources.rows} pending={resources.pending} t={t} lang={lang} groupName={monitor.groupName} />
        <div className="flex min-w-0 flex-[1.6_1_420px] flex-col bg-console">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3.5 py-2.5">
            <span className="flex flex-1 items-center gap-2 text-[13px] font-semibold text-console-fg">
              <span className={cn("h-[7px] w-[7px] rounded-full", running ? "animate-soft-pulse bg-success" : "bg-faint")} />
              {text.log}
            </span>
            {(["all", "warn", "err"] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs",
                  // The console stays dark in both themes: fixed colours, as in the mockup.
                  filter === key ? "border-[#7C6CFF73] bg-[#7C6CFF2E] text-[#B0A7FF]" : "border-[#3A3A42] text-[#A1A1AA] hover:text-console-fg",
                )}
              >
                {text.filters[key]}
              </button>
            ))}
            <button type="button" onClick={download} aria-label={text.download} title={text.download} className="p-1 text-faint hover:text-console-fg">
              <Download className="h-[15px] w-[15px]" />
            </button>
          </div>
          <LogLines entries={visible} templates={t.runLog as Record<string, string>} empty={text.emptyLog} lang={lang} />
        </div>
      </div>

      {confirmCancel ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-border bg-danger/15 px-[22px] py-3">
          <span className="flex-1 text-[13px] text-danger-text">{text.cancelConfirm}</span>
          <button type="button" onClick={() => setConfirmCancel(false)} className="rounded-lg border border-border bg-surface px-3 py-[7px] text-[13px] font-medium hover:bg-subtle">
            {text.keepGoing}
          </button>
          <button
            type="button"
            disabled={cancelling}
            onClick={() => void doCancel()}
            className="flex items-center gap-2 rounded-lg bg-danger px-3 py-[7px] text-[13px] font-medium text-white hover:bg-danger/90"
          >
            {cancelling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {text.confirmCancel}
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 border-t border-border px-[22px] py-3.5">
        {!eject ? (
          <label className="flex items-center gap-2 text-[13px] text-muted">
            <input type="checkbox" checked={Boolean(run.auto_eject_after_success)} disabled readOnly className="h-4 w-4 accent-[rgb(var(--pbo-accent))]" />
            {text.ejectAtEnd}
          </label>
        ) : null}
        <span className="flex-1" />
        {run.status === "failed" && (run.current_step === "verify_failed" || run.current_step === "disk_pulled") ? (
          <RepairButton diskId={run.disk_id} t={t} />
        ) : null}
        {running ? (
          <>
            {run.cancellable ? (
              <button
                type="button"
                onClick={() => setConfirmCancel(true)}
                className="rounded-lg bg-danger/15 px-3.5 py-[9px] text-sm font-medium text-danger-text hover:bg-danger/25"
              >
                {text.cancel}
              </button>
            ) : null}
            <button type="button" onClick={monitor.minimize} className="rounded-lg bg-accent px-3.5 py-[9px] text-sm font-medium text-accent-fg hover:bg-accent/90">
              {text.minimizeContinue}
            </button>
          </>
        ) : (
          <button type="button" onClick={monitor.close} className="rounded-lg bg-accent px-3.5 py-[9px] text-sm font-medium text-accent-fg hover:bg-accent/90">
            {text.close}
          </button>
        )}
      </div>
    </>
  );
}

const STEP_STYLE: Record<StepState, { circle: string; label: string }> = {
  done: { circle: "border-transparent bg-success/15 text-success-text", label: "text-fg" },
  active: { circle: "border-accent bg-accent text-accent-fg", label: "text-fg" },
  upcoming: { circle: "border-border-strong text-faint", label: "text-faint" },
  failed: { circle: "border-transparent bg-danger/15 text-danger-text", label: "text-fg" },
  skipped: { circle: "border-dashed border-border-strong text-faint", label: "text-faint" },
  not_run: { circle: "border-dashed border-border-strong text-faint", label: "text-faint line-through" },
};

export function StepList({ states, steps = RUN_STEPS, t }: { states: StepState[]; steps?: readonly RunStepKey[]; t: TranslationDictionary }) {
  const text = t.runModal;
  return (
    <ol aria-label={text.stepsLabel} className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2 border-y border-border bg-bg px-[22px] py-3.5">
      {steps.map((key, index) => {
        const state = states[index];
        return (
          <li key={key} className="flex items-center gap-2.5" data-state={state}>
            <span className={cn("flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border text-xs font-semibold", STEP_STYLE[state].circle)}>
              {state === "done" ? "✓" : state === "failed" ? "!" : state === "skipped" || state === "not_run" ? "–" : index + 1}
            </span>
            <span className="flex min-w-0 flex-col">
              <span className={cn("text-[13px] font-medium", STEP_STYLE[state].label)}>{text.steps[key]}</span>
              <span className="text-[11px] text-faint">{state === "skipped" ? text.stepSub.skipped : state === "not_run" ? text.stepSub.notRun : text.stepSub[key]}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ResourceList({
  rows,
  pending,
  t,
  lang,
  groupName,
}: {
  rows: ResourceRow[];
  pending: number;
  t: TranslationDictionary;
  lang: "fr" | "en";
  groupName: (group: string) => string | undefined;
}) {
  const text = t.runModal;
  const done = rows.filter((row) => row.state === "done").length;
  return (
    <div className="flex min-w-0 flex-[1_1_320px] flex-col border-r border-border">
      <div className="flex items-center px-[18px] py-3 text-[13px] font-semibold">
        <span className="flex-1">{text.resources}</span>
        {rows.length ? <span className="font-normal text-faint">{fill(text.resourcesDone, { done, total: rows.length + pending })}</span> : null}
      </div>
      <div className="flex max-h-[360px] flex-col overflow-auto">
        {rows.length === 0 ? <p className="px-[18px] pb-4 text-[13px] text-faint">{text.noResources}</p> : null}
        {rows.map((row) => (
          <div key={row.group} className={cn("flex items-center gap-2.5 px-[18px] py-2", row.state === "running" && "bg-accent/10")}>
            <span
              className={cn(
                "flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full text-[10px]",
                row.state === "done" && "bg-success/15 text-success-text",
                row.state === "running" && "bg-accent text-accent-fg",
                row.state === "failed" && "bg-danger/15 text-danger-text",
                row.state === "pending" && "bg-subtle",
              )}
            >
              {row.state === "done" ? "✓" : row.state === "running" ? "•" : row.state === "failed" ? "!" : ""}
            </span>
            <span className="w-11 font-mono text-[11px] text-faint">{row.group}</span>
            <span className={cn("min-w-0 flex-1 truncate text-[13px]", row.state === "pending" ? "text-faint" : "text-fg")}>{groupName(row.group) ?? row.group}</span>
            <span className="font-mono text-xs text-faint">
              {row.state === "pending"
                ? text.waiting
                : row.state === "running" && !row.bytes
                  ? text.inProgress
                  : row.noChanges && !row.bytes
                    ? text.noChanges
                    : formatBytes(row.bytes, lang)}
            </span>
          </div>
        ))}
        {pending > 0 ? <p className="px-[18px] py-2 text-[13px] text-faint">{fill(text.pendingMore, { n: pending })}</p> : null}
      </div>
    </div>
  );
}

const LEVEL_COLOR = { info: "text-[#7DD3FC]", ok: "text-[#4ADE80]", warn: "text-[#FBBF24]", err: "text-[#F87171]" } as const;

export function LogLines({
  entries,
  templates,
  empty,
  lang = "en",
}: {
  entries: JournalEntry[];
  templates: Record<string, string>;
  empty: string;
  lang?: "fr" | "en";
}) {
  return (
    <div className="flex min-h-[320px] flex-1 flex-col justify-end gap-[3px] overflow-auto px-3.5 py-2.5 font-mono text-xs leading-[1.55]">
      {entries.length === 0 ? <p className="text-faint">{empty}</p> : null}
      {entries.slice(-400).map((entry, index) => (
        <div key={index} className="flex gap-2.5" data-level={entry.level} data-source={entry.source}>
          <span className="shrink-0 text-[#71717A]">{clockTime(entry.at)}</span>
          <span className={cn("w-[38px] shrink-0", LEVEL_COLOR[entry.level])}>{LEVEL_LABEL[entry.level]}</span>
          {entry.source === "pbs" ? <span className="mt-px h-[17px] shrink-0 self-start rounded bg-white/10 px-1 text-[10px] leading-[17px] text-[#A1A1AA]">PBS</span> : null}
          <span className="min-w-0 break-words text-console-fg">
            {entryText(entry, templates, lang)}
            {entry.count > 1 ? <span className="text-[#71717A]"> ×{entry.count}</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}
