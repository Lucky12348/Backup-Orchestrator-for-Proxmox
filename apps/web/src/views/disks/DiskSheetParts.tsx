import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";

import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { openRunModal } from "../../runs/openRun";
import type { ExternalBackupRun, ExternalDisk } from "../../types";
import { formatGib, formatPercent, usageParts } from "../../units";
import { useNow } from "../../ui/useNow";
import { StatusIcon, type StatusKind } from "../../ui/StatusIcon";
import { fill, formatWhen } from "../format";
import { diskHistory, type Tone } from "./diskModel";
import { capacitySummary, chartWorthShowing, historyPage, usageLevel, usageSeries, type HistoryKindFilter } from "./diskProfile";
import { UsageChart } from "./UsageChart";

const GLYPH: Record<Tone, { kind: StatusKind; className?: string }> = {
  ok: { kind: "success" },
  run: { kind: "running" },
  warn: { kind: "failed", className: "text-warning" },
  err: { kind: "failed" },
  off: { kind: "skipped" },
};
const KINDS: HistoryKindFilter[] = ["all", "backup", "verify", "repair"];

/**
 * Phase 5h.1: a disk's runs, newest first — 5 in the Disks panel, 10 in the
 * sheet, "Show more", "See all in Activity" (filtered on the disk), a type
 * filter in the sheet. Every line opens the run modal (keyboard too).
 */
export function DiskHistoryList({
  disk,
  runs,
  language,
  t,
  pageSize,
  filterable = false,
}: {
  disk: ExternalDisk;
  runs: ExternalBackupRun[];
  language: Language;
  t: TranslationDictionary;
  pageSize: number;
  filterable?: boolean;
}) {
  const text = t.disksV2;
  const now = useNow();
  const [kind, setKind] = useState<HistoryKindFilter>("all");
  const [shown, setShown] = useState(pageSize);
  const all = diskHistory(disk, runs, Number.MAX_SAFE_INTEGER);
  const page = historyPage(all, kind, shown);
  return (
    <div className="flex flex-col gap-1" data-history={disk.id}>
      {filterable && all.length ? (
        <div role="group" aria-label={text.historyFilter} className="mb-1 flex flex-wrap gap-1.5">
          {KINDS.map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={kind === key}
              onClick={() => {
                setKind(key);
                setShown(pageSize);
              }}
              className={cn(
                "rounded-md border border-solid px-2 py-0.5 text-xs",
                kind === key ? "border-border-strong bg-hover text-fg" : "border-border bg-transparent text-muted hover:text-fg",
              )}
            >
              {key === "all" ? text.historyAll : text.historyKind[key]}
            </button>
          ))}
        </div>
      ) : null}
      {page.items.length === 0 ? <p className="m-0 py-1 text-[13px] text-faint">{text.noHistory}</p> : null}
      {page.items.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => openRunModal(item.id)}
          className="flex items-center gap-2.5 rounded-md border-0 bg-transparent px-1 py-1.5 text-left text-[13px] text-fg outline-none hover:bg-hover focus-visible:bg-hover focus-visible:ring-2 focus-visible:ring-accent"
          data-history-run={item.id}
        >
          <StatusIcon kind={GLYPH[item.tone].kind} className={GLYPH[item.tone].className} />
          <span className="flex-1 text-muted">
            {text.historyKind[item.kind]} · {(text.historyStatus as Record<string, string>)[item.status] ?? item.status}
          </span>
          <span className="text-xs text-faint">{formatWhen(item.at, language, now, t.dashboardV2)}</span>
          <ChevronRight className="h-3.5 w-3.5 flex-none text-faint" />
        </button>
      ))}
      <div className="mt-1 flex flex-wrap items-center gap-3">
        {page.more ? (
          <button type="button" onClick={() => setShown(shown + pageSize)} className="rounded-md border-0 bg-transparent px-1 py-1 text-[13px] font-medium text-accent-text hover:underline">
            {text.showMore}
          </button>
        ) : null}
        {all.length ? (
          <Link to={`/activity?disk=${disk.id}`} className="text-[13px] text-muted no-underline hover:text-fg hover:underline">
            {text.seeAllActivity}
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Phase 5h.1: "Capacity" instead of "Usage over time": the used / free bar
 * with the real values, the average growth per backup and "full in ~N
 * backups"; the chart only from 3 measures over 2 days.
 */
export function CapacityCard({ disk, runs, language, t, className }: { disk: ExternalDisk; runs: ExternalBackupRun[]; language: Language; t: TranslationDictionary; className?: string }) {
  const text = t.diskSheet;
  const own = runs.filter((run) => run.disk_id === disk.id);
  const summary = capacitySummary(disk, own);
  const points = usageSeries(disk.usage_history);
  const level = usageLevel(summary ? summary.percent : null);
  return (
    <section className={className} aria-labelledby="capacity-title" data-capacity>
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 id="capacity-title" className="m-0 text-sm font-semibold">{text.capacity}</h2>
      </div>
      {summary ? (
        <>
          <div className="flex flex-col gap-1.5">
            <div className="h-2.5 overflow-hidden rounded-full bg-subtle" role="img" aria-label={fill(text.capacityUsed, { used: formatGib(summary.usedGb, language), total: formatGib(summary.totalGb, language) })}>
              <div
                className={cn("h-full rounded-full", { ok: "bg-accent", warning: "bg-warning", danger: "bg-danger" }[level])}
                style={{ width: `${Math.max(summary.percent > 0 ? 1 : 0, Math.min(100, summary.percent))}%` }}
              />
            </div>
            <div className="flex flex-wrap justify-between gap-2 text-[13px]">
              <span className="text-fg" data-capacity-used>
                {fill(text.capacityUsed, { used: formatGib(summary.usedGb, language), total: formatGib(summary.totalGb, language) })}
                <span className="text-muted"> · {formatPercent(summary.percent, language)}</span>
              </span>
              <span className="text-muted">{fill(text.capacityFree, { free: formatGib(summary.freeGb, language) })}</span>
            </div>
          </div>
          <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
            <div className="flex flex-col">
              <dt className="text-xs text-faint">{text.growthPerBackup}</dt>
              <dd className="m-0 text-fg">{summary.growthPerBackupGb !== null ? `+${formatGib(summary.growthPerBackupGb, language)}` : text.growthUnknown}</dd>
            </div>
            <div className="flex flex-col">
              <dt className="text-xs text-faint">{text.fullIn}</dt>
              <dd className="m-0 text-fg" data-full-in={summary.backupsUntilFull ?? ""}>
                {summary.backupsUntilFull !== null ? fill(text.fullInBackups, { n: summary.backupsUntilFull }) : text.fullInUnknown}
              </dd>
            </div>
          </dl>
        </>
      ) : (
        <p className="m-0 text-[13px] text-faint">{usageParts(disk.filesystem_used_gb, disk.filesystem_total_gb, language) ? null : text.capacityUnknown}</p>
      )}
      {chartWorthShowing(points) ? (
        <div className="flex flex-col gap-2 border-0 border-t border-solid border-border pt-3" data-capacity-chart>
          <span className="text-[13px] text-muted">{text.usage}</span>
          <UsageChart points={points} name={disk.display_name} language={language} t={t} />
        </div>
      ) : null}
    </section>
  );
}
