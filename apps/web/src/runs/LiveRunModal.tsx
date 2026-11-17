import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { RepairButton } from "../views/disks/DiskBits";
import { ArrowDown, Download, Loader2, Minimize2 } from "lucide-react";
import { DiskTag } from "../views/disks/DiskTag";
import { CloseButton } from "../ui/CloseButton";
import { StatusIcon, type StatusKind } from "../ui/StatusIcon";

import { cancelExternalBackupRun } from "../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { cn } from "../components/ui/utils";
import type { Language, TranslationDictionary } from "../i18n";
import { useToast } from "../ui/toast";
import { useRunMonitor } from "./RunMonitor";
import {
  clockTime,
  copySummary,
  currentActivity,
  isAtBottom,
  runPercent,
  entryText,
  filterEntries,
  focusStep,
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
  stepTimings,
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
  const content = useRef<HTMLDivElement | null>(null);
  const running = isRunning(run);
  // When the run ends, the "Minimize" button that held the focus disappears:
  // the focus goes to the dialog itself, not to the X (5i: stray "|x" mark).
  useEffect(() => {
    if (open && !running) content.current?.focus({ preventScroll: true });
  }, [open, running]);
  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? (run && isRunning(run) ? monitor.minimize() : monitor.close()) : undefined)}>
      {run ? (
        <DialogContent
          ref={content}
          className="max-w-[1040px] outline-none"
          aria-describedby="run-subtitle"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            content.current?.focus({ preventScroll: true });
          }}
        >
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
  const steps = runSteps(run);
  const timings = stepTimings(run, entries, steps, states, now);
  // B35: the whole run, weighted by step (the copy's own % inside its step).
  const percent = runPercent(run, entries);
  const resources = resourceRows(run, entries);
  const summary = monitor.journal?.summary ?? null;
  const source = monitor.journal?.source_datastore ?? run.source_datastore;
  const target = monitor.journal?.target_datastore ?? run.target_datastore;
  const diskName = monitor.diskName(run.disk_id) ?? run.disk_name ?? `#${run.disk_id}`;
  const activity = currentActivity(steps, states, entries);
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

  const headerKind: StatusKind = running ? "running" : run.status === "success" ? "success" : "failed";

  const statsLine = [
    summary
      ? copySummary(summary, text, lang)
      : stats.transferredBytes !== null
        ? fill(text.transferred, { bytes: formatBytes(stats.transferredBytes, lang) })
        : null,
    formatRate(stats.rate, lang),
    running && stats.remainingSeconds !== null ? fill(text.remaining, { duration: formatDuration(stats.remainingSeconds, lang) ?? "" }) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const visible = filterEntries(entries, filter);

  // One line under the bar: "Step 5 of 7 · Cleanup · retention and GC · 42 s".
  const focus = focusStep(states);
  const focusKey = steps[focus];
  const focusDuration = formatDuration(timings[focus], lang);
  const stepLine =
    run.status === "success"
      ? fill(text.finishedIn, { duration: formatDuration(stats.elapsedSeconds, lang) ?? "—" })
      : [
          fill(text.stepOf, { n: focus + 1, total: steps.length }),
          text.steps[focusKey],
          run.status === "failed" ? text.stepFailed : (text.stepSub as Record<string, string>)[focusKey],
          run.status === "failed" ? null : focusDuration,
        ]
          .filter(Boolean)
          .join(" · ");

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
      <div className="flex items-start gap-3 px-6 pb-3 pt-5">
        <StatusIcon kind={headerKind} progress={running ? percent : undefined} size={20} className="mt-[3px]" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <DialogTitle className="m-0 text-[17px] font-semibold tracking-[-0.01em]">{title}</DialogTitle>
          <DialogDescription id="run-subtitle" className="m-0 flex flex-wrap items-center gap-x-1.5 text-[13px] text-muted">
            <span>{eject ? text.kind.eject : run.repair ? text.kind.repair : run.verify_only ? text.kind.verify : text.kind.backup}</span>
            <span aria-hidden>·</span>
            <span>{fill(text.subtitle, { time: clockTime(run.started_at.endsWith("Z") ? run.started_at : `${run.started_at}Z`) })}</span>
            <span aria-hidden>·</span>
            <DiskTag id={run.disk_id} fallback={diskName} t={t} size="xs" nameClassName="text-fg" />
          </DialogDescription>
        </div>
        {running ? (
          <button
            type="button"
            onClick={monitor.minimize}
            aria-label={text.minimize}
            title={text.minimize}
            className="flex items-center gap-1.5 rounded-md border border-solid border-border bg-transparent px-2.5 py-1.5 text-[13px] font-medium text-muted outline-none hover:bg-hover hover:text-fg focus-visible:ring-2 focus-visible:ring-focus"
          >
            <Minimize2 className="h-4 w-4" />
            <span className="hidden sm:inline">{text.minimize}</span>
          </button>
        ) : null}
        <CloseButton label={text.close} onClick={running ? monitor.minimize : monitor.close} />
      </div>

      <div className="flex flex-col gap-2.5 px-6 pb-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-[36px] font-semibold leading-none tracking-[-0.03em]" data-percent={Math.floor(percent)}>{Math.floor(percent)} %</span>
          {statsLine ? <span className="text-[13px] text-muted" data-stats>{statsLine}</span> : null}
        </div>
        <StepBar run={run} steps={steps} states={states} timings={timings} t={t} lang={lang} />
        <p className="m-0 text-[13px] text-muted" data-step-line>{stepLine}</p>
        <details className="text-[13px] text-muted" data-run-details>
          <summary className="w-fit cursor-pointer select-none text-faint hover:text-fg">{text.details}</summary>
          <dl className="m-0 mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            {target ? (
              <>
                <dt className="text-faint">{text.route}</dt>
                <dd className="m-0 font-mono text-xs text-fg [overflow-wrap:anywhere]" data-route>
                  {source ? `${source} → ` : ""}
                  {target}
                </dd>
              </>
            ) : null}
            <dt className="text-faint">{text.runId}</dt>
            <dd className="m-0 text-fg">#{run.id}</dd>
          </dl>
        </details>
      </div>

      <div className="flex min-h-0 flex-1 flex-wrap overflow-auto border-0 border-t border-solid border-border">
        {resources.rows.length ? (
          <ResourceList rows={resources.rows} pending={resources.pending} t={t} lang={lang} groupName={monitor.groupName} />
        ) : (
          <ActivityPanel activity={activity} running={running} t={t} lang={lang} summary={summary ? copySummary(summary, text, lang) : null} />
        )}
        <div className="flex min-w-0 flex-[1.6_1_420px] flex-col bg-console">
          <div className="flex flex-wrap items-center gap-1.5 border-0 border-b border-solid border-border px-3.5 py-2">
            <span className="flex flex-1 items-center gap-2 text-[13px] font-medium text-console-fg">
              <span className={cn("h-[7px] w-[7px] rounded-full", running ? "animate-soft-pulse bg-success" : "bg-faint")} />
              {text.log}
            </span>
            {(["all", "warn", "err"] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                aria-pressed={filter === key}
                className={cn(
                  "rounded-md border border-solid px-2 py-0.5 text-xs",
                  // 5i: the journal follows the theme (tokens only).
                  filter === key ? "border-border-strong bg-hover text-console-fg" : "border-transparent bg-transparent text-faint hover:text-console-fg",
                )}
              >
                {text.filters[key]}
              </button>
            ))}
            <button type="button" onClick={download} aria-label={text.download} title={text.download} className="rounded-md border-0 bg-transparent p-1 text-faint hover:text-console-fg">
              <Download className="h-4 w-4" />
            </button>
          </div>
          <LogLines entries={visible} templates={t.runLog as Record<string, string>} empty={text.emptyLog} lang={lang} follow={running} resumeLabel={text.resumeFollow} />
        </div>
      </div>

      {confirmCancel ? (
        <div className="flex flex-wrap items-center gap-3 border-0 border-t border-solid border-border bg-danger/10 px-6 py-3">
          <span className="flex-1 text-[13px] text-danger-text">{text.cancelConfirm}</span>
          <button type="button" onClick={() => setConfirmCancel(false)} className="rounded-lg border border-border bg-surface px-3 py-1.5 text-[13px] font-medium hover:bg-subtle">
            {text.keepGoing}
          </button>
          <button
            type="button"
            disabled={cancelling}
            onClick={() => void doCancel()}
            className="flex items-center gap-2 rounded-lg bg-danger px-3 py-1.5 text-[13px] font-medium text-white hover:bg-danger/90"
          >
            {cancelling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {text.confirmCancel}
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 border-0 border-t border-solid border-border px-6 py-3">
        {!eject ? (
          <label className="flex items-center gap-2 text-[13px] text-muted">
            <input type="checkbox" checked={Boolean(run.auto_eject_after_success)} disabled readOnly className="h-4 w-4 accent-accent" />
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
                className="rounded-md border border-solid border-border bg-transparent px-3 py-1.5 text-[13px] font-medium text-danger-text hover:bg-hover"
              >
                {text.cancel}
              </button>
            ) : null}
            <button type="button" onClick={monitor.minimize} className="rounded-md border-0 bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg hover:bg-accent/90">
              {text.minimizeContinue}
            </button>
          </>
        ) : (
          <button type="button" onClick={monitor.close} className="rounded-md border-0 bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg hover:bg-accent/90">
            {text.close}
          </button>
        )}
      </div>
    </>
  );
}

/** Segment colour per step state ("done" turns green once the whole run succeeded). */
function segmentClass(state: StepState, runSucceeded: boolean): string {
  switch (state) {
    case "done":
      return runSucceeded ? "bg-success" : "bg-accent";
    case "active":
      return "bg-accent/20";
    case "failed":
      return "bg-danger";
    case "skipped":
      return "pbo-hatch";
    case "not_run":
      return "bg-border opacity-60";
    default:
      return "bg-border";
  }
}

/**
 * 5i: the steps as ONE segmented bar (docs/design/mockups/live-run-modal.html):
 * filled = done, animated = in progress, hatched = skipped, red = failed.
 * Hovering a segment shows its name and duration.
 */
export function StepBar({
  run,
  steps = RUN_STEPS,
  states,
  timings = [],
  t,
  lang = "en",
}: {
  run?: Pick<RunLike, "status" | "progress_percent">;
  steps?: readonly RunStepKey[];
  states: StepState[];
  timings?: (number | null)[];
  t: TranslationDictionary;
  lang?: "fr" | "en";
}) {
  const text = t.runModal;
  const succeeded = run?.status === "success";
  return (
    <ol aria-label={text.stepsLabel} className="m-0 flex list-none gap-[3px] p-0" data-step-bar>
      {steps.map((key, index) => {
        const state = states[index];
        const seconds = timings[index] ?? null;
        const word =
          state === "skipped" ? text.stepSub.skipped : state === "not_run" ? text.stepSub.notRun : state === "upcoming" ? text.waiting : state === "failed" ? text.stepFailed : null;
        const label = [text.steps[key], seconds !== null ? formatDuration(seconds, lang) : null, word].filter(Boolean).join(" · ");
        const copy = key === "sync" && typeof run?.progress_percent === "number" ? Math.max(2, Math.min(100, run.progress_percent)) : null;
        return (
          <li key={key} className="group relative min-w-0 flex-1 py-1.5" data-state={state} data-step={key}>
            <span className={cn("relative block h-1.5 overflow-hidden rounded-full", segmentClass(state, succeeded))}>
              {state === "active" ? (
                copy !== null ? (
                  <span className="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${copy}%` }} />
                ) : (
                  <span className="pbo-indeterminate absolute inset-0" />
                )
              ) : null}
            </span>
            <span className="sr-only">{label}</span>
            <span
              aria-hidden
              className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-solid border-border bg-surface px-2 py-1 text-xs text-fg opacity-0 shadow-md transition-opacity group-hover:visible group-hover:opacity-100"
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

const ROW_KIND: Record<ResourceRow["state"], StatusKind> = { done: "success", running: "running", failed: "failed", pending: "pending" };

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
      <div className="flex items-center px-4 py-3 text-[13px] font-semibold">
        <span className="flex-1">{text.resources}</span>
        {rows.length ? <span className="font-normal text-faint">{fill(text.resourcesDone, { done, total: rows.length + pending })}</span> : null}
      </div>
      <div className="flex max-h-[360px] flex-col overflow-auto">
        {rows.length === 0 ? <p className="px-4 pb-4 text-[13px] text-faint">{text.noResources}</p> : null}
        {rows.map((row) => (
          <div key={row.group} className={cn("flex items-center gap-2.5 px-4 py-2", row.state === "running" && "bg-hover")} data-resource={row.state}>
            <StatusIcon kind={ROW_KIND[row.state]} />
            <span className="w-11 font-mono text-[11px] text-faint">{row.group}</span>
            <span className={cn("min-w-0 flex-1 truncate text-[13px]", row.state === "pending" ? "text-faint" : "text-fg")}>{groupName(row.group) ?? row.group}</span>
            <span className="text-xs text-faint">
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
        {pending > 0 ? <p className="px-4 py-2 text-[13px] text-faint">{fill(text.pendingMore, { n: pending })}</p> : null}
      </div>
    </div>
  );
}

const LEVEL_COLOR = { info: "text-accent-text", ok: "text-success-text", warn: "text-warning-text", err: "text-danger-text" } as const;

/**
 * B36: while the run goes on and the reader is at the bottom, the journal
 * follows the new lines. Scrolling up pauses it ("Resume following ↓"); once
 * the run is over, the scroll is left alone.
 */
export function LogLines({
  entries,
  templates,
  empty,
  lang = "en",
  follow = false,
  resumeLabel,
}: {
  entries: JournalEntry[];
  templates: Record<string, string>;
  empty: string;
  lang?: "fr" | "en";
  follow?: boolean;
  resumeLabel?: string;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const shown = entries.slice(-400);
  const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const toBottom = (smooth: boolean) => {
    const element = box.current;
    if (element) element.scrollTo({ top: element.scrollHeight, behavior: smooth && !reduce ? "smooth" : "auto" });
  };
  useLayoutEffect(() => {
    if (follow && atBottom) toBottom(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown.length, shown[shown.length - 1]?.count, follow]);
  return (
    <div className="relative flex min-h-[320px] flex-1 flex-col">
      <div
        ref={box}
        onScroll={(event) => {
          const element = event.currentTarget;
          setAtBottom(isAtBottom(element.scrollTop, element.clientHeight, element.scrollHeight));
        }}
        className="absolute inset-0 overflow-auto px-3.5 py-2.5 font-mono text-xs leading-[1.55]"
        data-log
        data-following={follow && atBottom ? "" : undefined}
      >
        <div className="flex min-h-full flex-col justify-end gap-[3px]">
      {entries.length === 0 ? <p className="text-faint">{empty}</p> : null}
      {shown.map((entry, index) => (
        <div key={index} className="flex gap-2.5" data-level={entry.level} data-source={entry.source}>
          <span className="shrink-0 text-faint">{clockTime(entry.at)}</span>
          <span className={cn("w-[38px] shrink-0", LEVEL_COLOR[entry.level])}>{LEVEL_LABEL[entry.level]}</span>
          {entry.source === "pbs" ? <span className="mt-px h-[17px] shrink-0 self-start rounded bg-fg/10 px-1 text-[10px] leading-[17px] text-muted">PBS</span> : null}
          <span className="min-w-0 break-words text-console-fg">
            {entryText(entry, templates, lang)}
            {entry.count > 1 ? <span className="text-faint"> ×{entry.count}</span> : null}
          </span>
        </div>
      ))}
        </div>
      </div>
      {follow && !atBottom && resumeLabel ? (
        <button
          type="button"
          onClick={() => {
            setAtBottom(true);
            toBottom(true);
          }}
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border-0 bg-accent px-3 py-1.5 font-sans text-xs font-medium text-white shadow-lg hover:brightness-110"
          data-resume
        >
          {resumeLabel}
          <ArrowDown className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/** Left panel when no VM/CT is being copied (verification, repair, nothing to
 *  copy): the step in progress and what it is doing. */
function ActivityPanel({
  activity,
  running,
  t,
  lang,
  summary,
}: {
  activity: ReturnType<typeof currentActivity>;
  running: boolean;
  t: TranslationDictionary;
  lang: "fr" | "en";
  summary: string | null;
}) {
  const text = t.runModal;
  const step = activity.step;
  return (
    <div className="flex min-w-0 flex-[1_1_320px] flex-col border-r border-border" data-activity-panel={step ?? ""}>
      <div className="flex items-center px-4 py-3 text-[13px] font-semibold">
        <span className="flex-1">{running ? text.nowDoing : text.whatHappened}</span>
      </div>
      <div className="flex flex-col gap-2 px-4 pb-4">
        {step ? (
          <span className="flex items-center gap-2 text-[13px] font-medium text-fg">
            <StatusIcon kind={running ? "running" : "success"} />
            {(text.steps as Record<string, string>)[step] ?? step}
            <span className="font-normal text-faint">· {(text.stepSub as Record<string, string>)[step] ?? ""}</span>
          </span>
        ) : null}
        {summary ? <span className="text-[13px] text-muted">{summary}</span> : null}
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {activity.lines.map((entry, index) => (
            <li key={index} className="text-[12px] leading-snug text-muted [overflow-wrap:anywhere]">
              {entryText(entry, t.runLog as Record<string, string>, lang)}
            </li>
          ))}
        </ul>
        {!activity.lines.length ? <p className="m-0 text-[13px] text-faint">{text.noResources}</p> : null}
      </div>
    </div>
  );
}
