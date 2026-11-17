
import type { TranslationDictionary } from "../i18n";
import { useRunMonitor } from "./RunMonitor";
import { isRunning, RUN_STEPS, runPercent, stepIndex, type JournalEntry } from "./runModel";
import { StatusIcon } from "../ui/StatusIcon";
import { DiskMark } from "../views/disks/DiskTag";

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
}

/** Top-bar "task in progress" pill: visible on every page while a run runs; reopens the modal. */
export function RunPill({ t }: { t: TranslationDictionary }) {
  const monitor = useRunMonitor();
  const run = monitor.activeRun;
  if (!run || !isRunning(run) || monitor.state.open) return null;
  // B35: the same weighted percentage as the modal.
  const entries = (monitor.journal?.run_id === run.id ? monitor.journal.entries : []) as JournalEntry[];
  const percent = Math.floor(runPercent(run, entries));
  const ejecting = stepIndex(run.current_step) === RUN_STEPS.indexOf("eject");
  const disk = monitor.diskName(run.disk_id) ?? run.disk_name ?? `#${run.disk_id}`;
  const kind = run.repair ? t.runModal.kind.repair : run.verify_only ? t.runModal.kind.verify : ejecting ? t.runModal.kind.eject : t.runModal.kind.backup;
  const label = fill(t.runModal.pill, { kind, disk });
  return (
    <button
      type="button"
      onClick={() => monitor.openRun(run.id)}
      title={t.runModal.open}
      className="flex items-center gap-2.5 rounded-md border border-solid border-border bg-surface py-1.5 pl-2.5 pr-3 text-[13px] font-medium text-fg hover:bg-hover"
    >
      <StatusIcon kind="running" progress={percent} />
      <DiskMark id={run.disk_id} t={t} />
      <span className="min-w-0 max-w-[220px] truncate">{label}</span>
      <span className="hidden h-1 w-16 overflow-hidden rounded-full bg-border sm:block">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
      </span>
      <span className="whitespace-nowrap text-xs text-muted">{percent} %</span>
    </button>
  );
}
