import { Loader2 } from "lucide-react";

import type { TranslationDictionary } from "../i18n";
import { useRunMonitor } from "./RunMonitor";
import { isRunning, RUN_STEPS, stepIndex } from "./runModel";

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
}

/** Top-bar "task in progress" pill: visible on every page while a run runs; reopens the modal. */
export function RunPill({ t }: { t: TranslationDictionary }) {
  const monitor = useRunMonitor();
  const run = monitor.activeRun;
  if (!run || !isRunning(run) || monitor.state.open) return null;
  const percent = Math.max(0, Math.min(100, Math.floor(run.progress_percent ?? 0)));
  const ejecting = stepIndex(run.current_step) === RUN_STEPS.indexOf("eject");
  const disk = monitor.diskName(run.disk_id) ?? run.disk_name ?? `#${run.disk_id}`;
  const label = fill(t.runModal.pill, { kind: ejecting ? t.runModal.kind.eject : t.runModal.kind.backup, disk });
  return (
    <button
      type="button"
      onClick={() => monitor.openRun(run.id)}
      title={t.runModal.open}
      className="flex items-center gap-2.5 rounded-full border border-accent/35 bg-accent/15 py-1.5 pl-2.5 pr-3 text-[13px] font-medium text-accent-text hover:brightness-110"
    >
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      <span className="max-w-[220px] truncate">{label}</span>
      <span className="block h-1 w-16 overflow-hidden rounded-full bg-accent/25">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
      </span>
      <span className="font-mono text-xs">{percent} %</span>
    </button>
  );
}
