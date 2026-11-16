import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, CheckCircle2, HardDrive, Loader2, Search, Trash2, Unplug, XCircle } from "lucide-react";
import { Link } from "react-router-dom";

import { getActivityFeed, type ActivityItem } from "../../api";
import { cn } from "../../components/ui/utils";
import { HANDOFF_CODES } from "../../handoff";
import type { Language, TranslationDictionary } from "../../i18n";
import { openRunModal } from "../../runs/openRun";
import { useConfirm } from "../../ui/confirm";
import { InfoTip } from "../../ui/InfoTip";
import { formatWhen } from "../format";
import {
  ACTIVITY_STATES,
  ACTIVITY_TYPES,
  PERIODS,
  activityDisks,
  activityState,
  activitySummary,
  durationText,
  filterActivity,
  type ActivityFilters,
  type ActivityState,
  type ActivityType,
} from "./activityModel";

const STATE_ICON: Record<ActivityState, { icon: typeof CheckCircle2; className: string }> = {
  ok: { icon: CheckCircle2, className: "bg-success/15 text-success-text" },
  failed: { icon: XCircle, className: "bg-danger/15 text-danger-text" },
  running: { icon: Loader2, className: "bg-accent/15 text-accent-text" },
  info: { icon: HardDrive, className: "bg-subtle text-muted" },
};
const TYPE_ICON: Record<ActivityType, typeof HardDrive> = { backup: HardDrive, eject: Unplug, detection: HardDrive, planning: CalendarClock };

/** Failure step code → its translated label (usb_device_not_found…). */
function handoffStepTexts(t: TranslationDictionary): Record<string, string> {
  const entries = HANDOFF_CODES.map((code) => [code, t.handoff[code].step]);
  return Object.fromEntries(entries);
}

const select = "h-8 rounded-lg border border-solid border-border bg-subtle px-2 text-[13px] text-fg outline-none focus:border-accent";

export interface ActivityViewProps {
  language: Language;
  t: TranslationDictionary;
  refreshKey: string;
  /** Deletes the old runs (keeps the 10 latest of each kind); returns when done. */
  onCleanup: () => Promise<void> | void;
  cleaning: boolean;
  fetchFeed?: typeof getActivityFeed;
  now?: Date;
}

export function ActivityView({ language, t, refreshKey, onCleanup, cleaning, fetchFeed = getActivityFeed, now: fixedNow }: ActivityViewProps) {
  const text = t.activityV2;
  const confirm = useConfirm();
  const [items, setItems] = useState<ActivityItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [filters, setFilters] = useState<ActivityFilters>({ type: "", state: "", diskId: null, days: 30, query: "" });
  const now = fixedNow ?? new Date();
  const fetchDays = Math.max(90, filters.days);

  const load = useCallback(async () => {
    try {
      setItems(await fetchFeed(fetchDays));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [fetchFeed, fetchDays]);
  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const stepTexts = useMemo(() => handoffStepTexts(t), [t]);
  const summary = (item: ActivityItem) => activitySummary(item, text.summary, language, stepTexts);
  const shown = filterActivity(items ?? [], filters, now, summary);
  const disks = activityDisks(items ?? []);
  const set = (patch: Partial<ActivityFilters>) => setFilters((current) => ({ ...current, ...patch }));

  async function cleanup() {
    if (!(await confirm({ title: text.cleanupTitle, description: text.cleanupText, confirmLabel: text.cleanupConfirm, tone: "danger" }))) return;
    await onCleanup();
    await load();
  }

  return (
    <div className="flex flex-col gap-[18px]">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em]">{text.title}</h1>
        <InfoTip text={text.info} label={t.shell.about} />
        <span className="flex-1" />
        <button
          type="button"
          disabled={cleaning}
          onClick={() => void cleanup()}
          className="flex items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3.5 py-[9px] text-sm font-medium text-fg hover:brightness-110 disabled:opacity-60"
        >
          <Trash2 className="h-4 w-4" />
          {text.cleanup}
        </button>
      </div>

      <div role="group" aria-label={text.filters} className="flex flex-wrap items-center gap-2">
        <label className="flex h-8 min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-2.5 text-faint sm:max-w-[320px]">
          <Search className="h-3.5 w-3.5" />
          <input
            aria-label={text.search}
            placeholder={text.search}
            value={filters.query}
            onChange={(event) => set({ query: event.target.value })}
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-fg outline-none placeholder:text-faint"
          />
        </label>
        <select aria-label={text.allTypes} className={select} value={filters.type} onChange={(event) => set({ type: event.target.value as ActivityType | "" })}>
          <option value="">{text.allTypes}</option>
          {ACTIVITY_TYPES.map((type) => (
            <option key={type} value={type}>
              {text.type[type]}
            </option>
          ))}
        </select>
        <select aria-label={text.allStates} className={select} value={filters.state} onChange={(event) => set({ state: event.target.value as ActivityState | "" })}>
          <option value="">{text.allStates}</option>
          {ACTIVITY_STATES.map((state) => (
            <option key={state} value={state}>
              {text.state[state]}
            </option>
          ))}
        </select>
        <select
          aria-label={text.allDisks}
          className={select}
          value={filters.diskId ?? ""}
          onChange={(event) => set({ diskId: event.target.value ? Number(event.target.value) : null })}
        >
          <option value="">{text.allDisks}</option>
          {disks.map((disk) => (
            <option key={disk.id} value={disk.id}>
              {disk.label}
            </option>
          ))}
        </select>
        <select aria-label={text.periodLabel} className={select} value={filters.days} onChange={(event) => set({ days: Number(event.target.value) })}>
          {PERIODS.map((days) => (
            <option key={days} value={days}>
              {text.period[String(days) as keyof typeof text.period]}
            </option>
          ))}
        </select>
      </div>

      <section className="overflow-hidden rounded-2xl border border-solid border-border bg-surface">
        {failed ? <p className="m-0 px-5 py-4 text-[13px] text-danger-text">{text.loadFailed}</p> : null}
        {items === null && !failed ? <p className="m-0 px-5 py-4 text-[13px] text-faint">{text.loading}</p> : null}
        {items !== null && shown.length === 0 ? (
          <p className="m-0 px-5 py-8 text-center text-[13px] text-faint">{items.length ? text.noMatch : text.empty}</p>
        ) : null}
        <ol className="m-0 list-none p-0">
          {shown.map((item) => (
            <ActivityRow key={item.id} item={item} summary={summary(item)} language={language} t={t} now={now} />
          ))}
        </ol>
      </section>
    </div>
  );
}

export function ActivityRow({ item, summary, language, t, now }: { item: ActivityItem; summary: string; language: Language; t: TranslationDictionary; now: Date }) {
  const text = t.activityV2;
  const state = activityState(item);
  const { icon: StateIcon, className } = STATE_ICON[state];
  const TypeIcon = TYPE_ICON[item.type];
  const duration = durationText(item.duration_seconds, language);
  const clickable = item.run_id !== null;
  const content = (
    <>
      <span className={cn("flex h-8 w-8 flex-none items-center justify-center rounded-full", className)}>
        <StateIcon className={cn("h-4 w-4", state === "running" && "animate-spin")} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px]">
          <span className="inline-flex items-center gap-1 font-medium">
            <TypeIcon className="h-3.5 w-3.5 text-faint" />
            {text.type[item.type]}
          </span>
          <span className="text-faint">·</span>
          <span className="truncate text-muted">{item.disk_label}</span>
          {item.planned && item.type !== "planning" ? <span className="rounded bg-subtle px-1.5 text-[11px] text-faint">{text.planned}</span> : null}
        </span>
        <span className={cn("truncate text-sm", state === "failed" ? "text-danger-text" : "text-fg")}>{summary}</span>
      </span>
      <span className="flex flex-none flex-col items-end gap-0.5 text-xs text-faint">
        <span>{formatWhen(item.at, language, now, t.dashboardV2)}</span>
        {duration ? <span className="font-mono">{duration}</span> : null}
      </span>
    </>
  );
  const row = "flex w-full items-center gap-3 border-0 border-b border-solid border-border bg-transparent px-5 py-3 text-left text-fg last:border-b-0";
  return (
    <li data-activity={item.id} data-type={item.type} data-state={state}>
      {clickable ? (
        <button type="button" className={cn(row, "hover:bg-subtle")} onClick={() => item.run_id && openRunModal(item.run_id)} aria-label={`${text.open}: ${summary}`}>
          {content}
        </button>
      ) : item.type === "planning" ? (
        <Link to="/planning" className={cn(row, "no-underline hover:bg-subtle")}>
          {content}
        </Link>
      ) : (
        <div className={row}>{content}</div>
      )}
    </li>
  );
}
