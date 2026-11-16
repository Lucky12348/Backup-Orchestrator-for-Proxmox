import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CalendarPlus, ChevronLeft, ChevronRight, Download, Share } from "lucide-react";

import {
  cancelScheduledBackupRun,
  confirmScheduledBackupRun,
  getAgenda,
  getCalendarFeed,
  regenerateCalendarFeed,
  runScheduledBackupNow,
  type AgendaResponse,
  type CalendarFeed,
} from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { openRunModal } from "../../runs/openRun";
import type { ExternalDisk, ScheduledBackupEvent, VirtualMachine } from "../../types";
import { useConfirm } from "../../ui/confirm";
import { Popover } from "../../ui/Popover";
import { useToast } from "../../ui/toast";
import { fill } from "../format";
import {
  KINDS,
  VIEWS,
  dayKey,
  feedUrl,
  filterKinds,
  itemActions,
  itemTone,
  hourLabel,
  itemsOn,
  layoutDay,
  monthGrid,
  nowMinutes,
  periodTitle,
  placeDay,
  sameDay,
  slotStart,
  stepAnchor,
  today,
  viewRange,
  visibleDays,
  weekdayName,
  weekdayOf,
  zoned,
  type AgendaItem,
  type CalendarView,
  type Day,
  type ItemAction,
  type ItemKind,
  type Overflow,
  type Tone,
} from "./calendarModel";
import { PlanningDialog, type DialogState } from "./PlanningDialog";

const HOUR_PX = 34;

export const TONE_CLASS: Record<Tone, string> = {
  planned: "bg-accent/15 text-accent-text",
  plannedMonthly: "bg-warning/15 text-warning-text",
  plannedManual: "bg-accent/10 text-accent-text",
  proxmox: "bg-subtle text-muted",
  success: "bg-success/15 text-success-text",
  failed: "bg-danger/15 text-danger-text",
  running: "pbo-stripes text-white",
  confirm: "bg-warning/20 text-warning-text outline-dashed outline-1 outline-warning",
  blocked: "bg-warning/10 text-warning-text line-through decoration-warning/60",
  muted: "bg-subtle text-faint line-through",
};

const TONE_MARK: Partial<Record<Tone, string>> = { success: "✓ ", failed: "✕ ", running: "● ", confirm: "? ", blocked: "⚠ " };

export const KIND_COLOR: Record<ItemKind, string> = {
  external: "rgb(var(--pbo-accent))",
  monthly: "rgb(var(--pbo-warning))",
  manual: "#9B8CFF",
  proxmox: "rgb(var(--pbo-faint))",
};

const HIDDEN_KEY = "pbo:planning-hidden";

function readHidden(): Set<ItemKind> {
  try {
    const value = JSON.parse(window.localStorage.getItem(HIDDEN_KEY) ?? "[]");
    return new Set((Array.isArray(value) ? value : []).filter((kind): kind is ItemKind => KINDS.includes(kind)));
  } catch {
    return new Set(["proxmox"]);
  }
}

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export interface PlanningViewProps {
  events: ScheduledBackupEvent[];
  disks: ExternalDisk[];
  vms: VirtualMachine[];
  defaultDatastore: string;
  language: Language;
  t: TranslationDictionary;
  /** Changes when runs change (SSE): the visible range is reloaded. */
  refreshKey: string;
  onChanged: () => Promise<void> | void;
  fetchAgenda?: typeof getAgenda;
  now?: Date;
}

/** First sentence, at most 90 characters (raw connection errors are long). */
function shortError(message: string): string {
  const first = message.replace(/\s+/g, " ").split(/(?<=[.:])\s/)[0] ?? message;
  return first.length > 90 ? `${first.slice(0, 89)}…` : first;
}

function errorText(error: unknown, t: TranslationDictionary): string {
  const table = t.planningV2.errors as Record<string, string>;
  if (error instanceof ApiError && error.code && table[error.code]) return table[error.code];
  return error instanceof Error ? error.message : t.error;
}

export function PlanningView(props: PlanningViewProps) {
  const { language, t } = props;
  const text = t.planningV2;
  const lang = language;
  const fetchAgenda = props.fetchAgenda ?? getAgenda;
  const { toast } = useToast();
  const [now, setNow] = useState(() => props.now ?? new Date());
  const [agenda, setAgenda] = useState<AgendaResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const tz = agenda?.timezone || browserZone();
  // A phone shows one day at a time (a week of 7 columns does not fit).
  const [view, setView] = useState<CalendarView>(() =>
    typeof window !== "undefined" && window.matchMedia?.("(max-width: 639px)").matches ? "day" : "week",
  );
  const [anchor, setAnchor] = useState<Day>(() => today(props.now ?? new Date(), browserZone()));
  const [hidden, setHidden] = useState<Set<ItemKind>>(readHidden);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const showProxmox = !hidden.has("proxmox");

  useEffect(() => {
    if (props.now) return;
    const timer = window.setInterval(() => setNow(new Date()), 60000);
    return () => window.clearInterval(timer);
  }, [props.now]);

  const range = useMemo(() => viewRange(view, anchor, tz), [view, anchor, tz]);
  const load = useCallback(async () => {
    try {
      setAgenda(await fetchAgenda(range.start, range.end, showProxmox));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [fetchAgenda, range.start, range.end, showProxmox]);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  function toggleKind(kind: ItemKind) {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      try {
        window.localStorage.setItem(HIDDEN_KEY, JSON.stringify([...next]));
      } catch {
        /* this page only */
      }
      return next;
    });
  }

  const items = filterKinds(agenda?.items ?? [], hidden);
  const selected = (agenda?.items ?? []).find((item) => item.id === selectedId) ?? null;
  const todayDay = today(now, tz);

  function openCreate(start: Date) {
    setDialog({ mode: "create", start });
  }

  async function act(item: AgendaItem, action: ItemAction) {
    try {
      if (action === "follow" || action === "report") {
        if (item.backup_run_id) openRunModal(item.backup_run_id);
        return;
      }
      if (action === "edit") {
        const event = props.events.find((candidate) => candidate.id === item.event_id);
        if (event) setDialog({ mode: "edit", event });
        return;
      }
      if (action === "confirm" && item.planning_run_id) {
        await confirmScheduledBackupRun(item.planning_run_id);
        toast({ tone: "success", title: text.confirmDone });
      } else if (action === "cancel" && item.planning_run_id) {
        await cancelScheduledBackupRun(item.planning_run_id);
        toast({ tone: "info", title: text.cancelDone });
      } else if (action === "run_now" && item.event_id) {
        const run = await runScheduledBackupNow(item.event_id);
        toast({ tone: "success", title: text.runNowDone });
        if (run.backup_run_id) openRunModal(run.backup_run_id);
      }
      await Promise.all([load(), props.onChanged()]);
    } catch (error) {
      toast({ tone: "danger", title: errorText(error, t) });
    }
  }

  return (
    <div className="-mx-4 -my-5 flex min-h-[calc(100vh-61px)] flex-wrap sm:-mx-7 sm:-my-7 2xl:-mx-10">
      <div className="order-2 flex flex-[1_1_250px] flex-col gap-[22px] border-solid border-border px-4 py-5 lg:order-1 lg:max-w-[290px] lg:border-r">
        <button
          type="button"
          onClick={() => openCreate(slotStart(anchor, 9, tz))}
          className="flex items-center gap-2.5 self-start rounded-[14px] border-0 bg-accent py-3 pl-3.5 pr-[18px] text-sm font-medium text-white shadow-[0_4px_16px_rgb(var(--pbo-accent)/0.3)] hover:brightness-110"
        >
          <CalendarPlus className="h-[18px] w-[18px]" />
          {text.newPlanning}
        </button>
        <MiniCalendar anchor={anchor} todayDay={todayDay} items={items} tz={tz} lang={lang} label={text.miniLabel} onPick={setAnchor} />
        <div className="flex flex-col gap-1">
          <span className="px-1 pb-1.5 text-[13px] font-semibold">{text.calendars}</span>
          {KINDS.map((kind) => (
            <label key={kind} className="flex cursor-pointer items-center gap-2.5 px-1 py-1.5 text-[13px] text-muted">
              <input
                type="checkbox"
                checked={!hidden.has(kind)}
                onChange={() => toggleKind(kind)}
                style={{ accentColor: KIND_COLOR[kind] }}
                className="m-0 h-4 w-4"
              />
              <span className="flex-1">{text.kind[kind]}</span>
              <span className="h-2 w-2 rounded-[3px]" style={{ background: KIND_COLOR[kind] }} />
            </label>
          ))}
          {showProxmox && agenda?.proxmox_error ? (
            <span className="px-1 text-xs text-warning-text">{fill(text.proxmoxUnavailable, { reason: shortError(agenda.proxmox_error) })}</span>
          ) : null}
          {showProxmox && agenda?.proxmox_unsupported.length ? (
            <span className="px-1 text-xs text-faint">{fill(text.proxmoxUnsupported, { jobs: agenda.proxmox_unsupported.join(", ") })}</span>
          ) : null}
        </div>
        {selected ? <DetailCard item={selected} tz={tz} lang={lang} t={t} onAction={(action) => void act(selected, action)} /> : null}
      </div>

      <div className="order-1 flex min-w-0 flex-[999_1_560px] flex-col lg:order-2">
        <div className="flex min-h-16 flex-wrap items-center gap-2.5 border-b border-solid border-border px-5 py-3">
          <button
            type="button"
            onClick={() => setAnchor(todayDay)}
            className="rounded-lg border border-solid border-border-strong bg-transparent px-3.5 py-[7px] text-[13px] font-medium text-fg hover:bg-hover"
          >
            {text.today}
          </button>
          <button type="button" aria-label={text.previous} title={text.previous} onClick={() => setAnchor(stepAnchor(view, anchor, -1))} className="flex h-8 w-8 items-center justify-center rounded-full border-0 bg-transparent text-muted hover:bg-hover">
            <ChevronLeft className="h-[18px] w-[18px]" />
          </button>
          <button type="button" aria-label={text.next} title={text.next} onClick={() => setAnchor(stepAnchor(view, anchor, 1))} className="flex h-8 w-8 items-center justify-center rounded-full border-0 bg-transparent text-muted hover:bg-hover">
            <ChevronRight className="h-[18px] w-[18px]" />
          </button>
          <h1 className="m-0 ml-1.5 flex-1 text-xl font-semibold tracking-[-0.01em]" data-period={view}>
            {periodTitle(view, anchor, lang)}
          </h1>
          <div role="tablist" aria-label={text.viewsLabel} className="flex rounded-[9px] border border-solid border-border bg-subtle p-[3px]">
            {VIEWS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={view === key}
                onClick={() => setView(key)}
                className={cn(
                  "rounded-[7px] border-0 px-3 py-1.5 text-[13px] font-medium",
                  view === key ? "bg-surface text-fg shadow-[0_1px_3px_rgba(0,0,0,0.25)]" : "bg-transparent text-muted hover:text-fg",
                )}
              >
                {text.view[key]}
              </button>
            ))}
          </div>
          <ExportPopover t={t} />
        </div>

        {failed ? <div className="px-5 py-3 text-[13px] text-danger-text">{text.loadFailed}</div> : null}
        {!agenda && !failed ? <div className="px-5 py-3 text-[13px] text-faint">{text.loading}</div> : null}

        {view === "day" || view === "week" ? (
          <TimeGrid
            days={visibleDays(view, anchor)}
            items={items}
            tz={tz}
            lang={lang}
            now={now}
            todayDay={todayDay}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onPickDay={(day) => {
              setAnchor(day);
              setView("day");
            }}
            onEmptySlot={(day, hour) => openCreate(slotStart(day, hour, tz))}
            emptyLabel={text.emptySlot}
            tzLabel={fill(text.timezone, { tz })}
            t={t}
            addAtLabel={text.addAt}
          />
        ) : null}
        {view === "month" ? (
          <MonthGrid
            anchor={anchor}
            items={items}
            tz={tz}
            lang={lang}
            todayDay={todayDay}
            selectedId={selectedId}
            moreLabel={text.more}
            t={t}
            onSelect={setSelectedId}
            onPickDay={(day) => {
              setAnchor(day);
              setView("day");
            }}
          />
        ) : null}
        {view === "year" ? (
          <YearGrid
            anchor={anchor}
            items={items}
            tz={tz}
            lang={lang}
            todayDay={todayDay}
            onPickDay={(day) => {
              setAnchor(day);
              setView("day");
            }}
          />
        ) : null}
      </div>

      <PlanningDialog
        state={dialog}
        onClose={() => setDialog(null)}
        onSaved={async () => {
          setDialog(null);
          await Promise.all([load(), props.onChanged()]);
        }}
        disks={props.disks}
        vms={props.vms}
        defaultDatastore={props.defaultDatastore}
        tz={tz}
        language={language}
        t={t}
      />
    </div>
  );
}

export function itemLabel(item: AgendaItem, t: TranslationDictionary): string {
  if (item.kind === "proxmox") return fill(t.planningV2.proxmoxTitle, { storage: item.disk_label || item.job_id || "" });
  return item.title || (item.kind === "manual" ? t.planningV2.manualTitle : item.kind);
}

function timeOf(iso: string, tz: string): string {
  const p = zoned(new Date(iso), tz);
  return `${String(p.h).padStart(2, "0")}:${String(p.min).padStart(2, "0")}`;
}

export function EventChip({
  item,
  tz,
  t,
  selected,
  onSelect,
  compact,
  style,
}: {
  item: AgendaItem;
  tz: string;
  t: TranslationDictionary;
  selected: boolean;
  onSelect: () => void;
  compact?: boolean;
  style?: CSSProperties;
}) {
  const tone = itemTone(item);
  const sub = item.status === "running" && item.progress_percent != null ? `${item.disk_label ?? ""} · ${Math.round(item.progress_percent)} %` : item.disk_label ?? "";
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      data-item={item.id}
      data-tone={tone}
      data-status={item.status}
      aria-pressed={selected}
      title={`${timeOf(item.starts_at, tz)} ${itemLabel(item, t)} · ${t.planningV2.status[item.status]}`}
      style={style}
      className={cn(
        "overflow-hidden rounded-md border-0 text-left hover:brightness-110",
        compact ? "w-full truncate px-1.5 py-[3px] text-[11px] font-medium" : "absolute flex flex-col gap-px px-[7px] py-[3px]",
        TONE_CLASS[tone],
        selected && "outline outline-2 outline-fg",
      )}
    >
      {compact ? (
        <>
          {timeOf(item.starts_at, tz)} {TONE_MARK[tone] ?? ""}
          {itemLabel(item, t)}
        </>
      ) : (
        <>
          <span className="truncate text-xs font-semibold">
            {TONE_MARK[tone] ?? ""}
            {itemLabel(item, t)}
          </span>
          <span className="truncate text-[11px] opacity-85">
            {timeOf(item.starts_at, tz)} · {sub}
          </span>
        </>
      )}
    </button>
  );
}

function TimeGrid({
  days,
  items,
  tz,
  lang,
  now,
  todayDay,
  selectedId,
  onSelect,
  onPickDay,
  onEmptySlot,
  emptyLabel,
  tzLabel,
  t,
  addAtLabel,
}: {
  t: TranslationDictionary;
  addAtLabel: string;
  days: Day[];
  items: AgendaItem[];
  tz: string;
  lang: Lang;
  now: Date;
  todayDay: Day;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPickDay: (day: Day) => void;
  onEmptySlot: (day: Day, hour: number) => void;
  emptyLabel: string;
  tzLabel: string;
}) {
  const scroller = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = 7 * HOUR_PX;
  }, []);
  // Readable minimum width per day column (3 side-by-side events stay legible).
  const columns = `56px repeat(${days.length}, minmax(${days.length > 1 ? 120 : 0}px, 1fr))`;
  const [hover, setHover] = useState<{ day: string; hour: number } | null>(null);
  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-x-auto">
      <div className="grid min-w-[520px] border-b border-solid border-border" style={{ gridTemplateColumns: columns }}>
        <div className="self-end px-1.5 py-2 text-[10px] text-faint" title={tzLabel}>
          {tz.split("/").pop()}
        </div>
        {days.map((day) => {
          const isToday = sameDay(day, todayDay);
          return (
            <button
              key={dayKey(day)}
              type="button"
              onClick={() => onPickDay(day)}
              className="flex flex-col items-center gap-1 border-0 border-l border-solid border-border bg-transparent pb-2.5 pt-2 text-fg"
            >
              <span className={cn("text-[11px] font-medium uppercase", isToday ? "text-accent-text" : "text-faint")}>
                {weekdayName(weekdayOf(day), lang, "short")}
              </span>
              <span className={cn("flex h-10 w-10 items-center justify-center rounded-full text-xl", isToday ? "bg-accent text-white" : "text-fg")}>
                {day.d}
              </span>
            </button>
          );
        })}
      </div>
      <div ref={scroller} className="max-h-[calc(100vh-240px)] min-h-[420px] overflow-y-auto">
        <div className="grid min-w-[520px]" style={{ gridTemplateColumns: columns }}>
          <div className="flex flex-col">
            {Array.from({ length: 24 }, (_, hour) => (
              <div key={hour} className="h-[34px] -translate-y-1.5 pr-2 text-right text-[10px] text-faint">
                {hour === 0 ? "" : `${String(hour).padStart(2, "0")}:00`}
              </div>
            ))}
          </div>
          {days.map((day) => {
            const isToday = sameDay(day, todayDay);
            const line = nowMinutes(now, day, tz);
            const layout = layoutDay(items, day, tz);
            return (
              <div
                key={dayKey(day)}
                role="presentation"
                title={emptyLabel}
                data-day={dayKey(day)}
                onClick={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  onEmptySlot(day, Math.floor((event.clientY - rect.top) / HOUR_PX));
                }}
                onMouseMove={(event) => {
                  if (event.target !== event.currentTarget) return setHover(null);
                  const rect = event.currentTarget.getBoundingClientRect();
                  const hour = Math.floor((event.clientY - rect.top) / HOUR_PX);
                  if (hover?.day !== dayKey(day) || hover.hour !== hour) setHover({ day: dayKey(day), hour });
                }}
                onMouseLeave={() => setHover(null)}
                className={cn("relative h-[816px] cursor-cell border-l border-solid border-border", isToday && "bg-accent/[0.04]")}
                style={{ backgroundImage: "repeating-linear-gradient(to bottom, rgb(var(--pbo-border)) 0 1px, transparent 1px 34px)" }}
              >
                {hover?.day === dayKey(day) ? (
                  <div
                    className="pointer-events-none absolute left-0 right-0 flex items-start rounded-sm bg-accent/10 px-1.5 pt-0.5 text-[11px] font-medium text-accent-text"
                    style={{ top: `${hover.hour * HOUR_PX}px`, height: `${HOUR_PX}px` }}
                    data-hover-slot={hourLabel(hover.hour)}
                  >
                    {fill(addAtLabel, { time: hourLabel(hover.hour) })}
                  </div>
                ) : null}
                {layout.overflow.map((group) => (
                  <OverflowChip
                    key={`more-${group.top}`}
                    group={group}
                    tz={tz}
                    t={t}
                    onSelect={onSelect}
                  />
                ))}
                {layout.placed.map((entry) => (
                  <EventChip
                    key={entry.item.id}
                    item={entry.item}
                    tz={tz}
                    t={t}
                    selected={selectedId === entry.item.id}
                    onSelect={() => onSelect(entry.item.id)}
                    style={{
                      top: `${(entry.top / 60) * HOUR_PX + 1}px`,
                      height: `${Math.max(32, (entry.height / 60) * HOUR_PX - 2)}px`,
                      left: `calc(${(entry.lane / entry.lanes) * 100}% + 3px)`,
                      width: `calc(${100 / entry.lanes}% - 9px)`,
                    }}
                  />
                ))}
                {line !== null ? (
                  <div className="pointer-events-none absolute left-0 right-0 h-0.5 bg-danger" style={{ top: `${(line / 60) * HOUR_PX}px` }} data-now-line="">
                    <span className="absolute -left-1.5 -top-[5px] h-3 w-3 rounded-full bg-danger" />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

type Lang = Language;

/** "+N" chip of an hour crowded with more than MAX_LANES events: opens their list. */
function OverflowChip({ group, tz, t, onSelect }: { group: Overflow; tz: string; t: TranslationDictionary; onSelect: (id: string) => void }) {
  return (
    <div
      className="absolute z-[1]"
      style={{
        top: `${(group.top / 60) * HOUR_PX + 1}px`,
        left: `calc(${(group.lane / group.lanes) * 100}% + 3px)`,
        width: `calc(${100 / group.lanes}% - 9px)`,
      }}
      onClick={(event) => event.stopPropagation()}
      onMouseMove={(event) => event.stopPropagation()}
    >
      <Popover
        label={t.planningV2.moreTitle}
        placement="bottom-end"
        className="w-[260px] p-1.5"
        triggerClassName="flex h-[32px] w-full items-center justify-center rounded-md border-0 bg-subtle text-xs font-semibold text-fg hover:bg-hover"
        trigger={<span data-overflow={group.items.length}>{fill(t.planningV2.moreShort, { n: group.items.length })}</span>}
      >
        {(close) => (
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {group.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    close();
                    onSelect(item.id);
                  }}
                  className={cn("w-full truncate rounded-md border-0 px-2 py-1.5 text-left text-xs font-medium hover:brightness-110", TONE_CLASS[itemTone(item)])}
                >
                  {timeOf(item.starts_at, tz)} {itemLabel(item, t)}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Popover>
    </div>
  );
}

function MonthGrid({
  anchor,
  items,
  tz,
  lang,
  todayDay,
  selectedId,
  moreLabel,
  onSelect,
  onPickDay,
  t,
}: {
  t: TranslationDictionary;
  anchor: Day;
  items: AgendaItem[];
  tz: string;
  lang: Lang;
  todayDay: Day;
  selectedId: string | null;
  moreLabel: string;
  onSelect: (id: string) => void;
  onPickDay: (day: Day) => void;
}) {
  return (
    <div className="grid flex-1 grid-cols-7">
      {Array.from({ length: 7 }, (_, weekday) => (
        <div key={weekday} className="border-l border-solid border-border py-2 text-center text-[11px] font-medium uppercase text-faint">
          {weekdayName(weekday, lang, "short")}
        </div>
      ))}
      {monthGrid(anchor.y, anchor.m).map((day) => {
        const dayItems = itemsOn(items, day, tz);
        const isToday = sameDay(day, todayDay);
        return (
          <div
            key={dayKey(day)}
            data-day={dayKey(day)}
            className={cn("flex min-h-[128px] min-w-0 flex-col gap-[3px] border-l border-t border-solid border-border p-1.5", day.m !== anchor.m && "opacity-45")}
          >
            <button
              type="button"
              onClick={() => onPickDay(day)}
              className={cn("h-[26px] w-[26px] self-center rounded-full border-0 text-xs font-medium", isToday ? "bg-accent text-white" : "bg-transparent text-fg hover:bg-hover")}
            >
              {day.d}
            </button>
            {dayItems.slice(0, 3).map((item) => (
              <EventChip key={item.id} item={item} tz={tz} t={t} compact selected={selectedId === item.id} onSelect={() => onSelect(item.id)} />
            ))}
            {dayItems.length > 3 ? (
              <button type="button" onClick={() => onPickDay(day)} className="border-0 bg-transparent px-1.5 text-left text-[11px] text-muted hover:text-fg">
                {fill(moreLabel, { n: dayItems.length - 3 })}
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function YearGrid({ anchor, items, tz, lang, todayDay, onPickDay }: { anchor: Day; items: AgendaItem[]; tz: string; lang: Lang; todayDay: Day; onPickDay: (day: Day) => void }) {
  const busy = useMemo(() => new Set(items.map((item) => dayKey(today(new Date(item.starts_at), tz)))), [items, tz]);
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-6 gap-y-7 p-6">
      {Array.from({ length: 12 }, (_, index) => {
        const month = { y: anchor.y, m: index + 1, d: 1 };
        return (
          <div key={index} className="flex flex-col gap-1.5">
            <span className="pl-1 text-sm font-semibold capitalize">
              {new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(anchor.y, index, 1)))}
            </span>
            <SmallMonth month={month} busy={busy} todayDay={todayDay} selected={null} lang={lang} onPick={onPickDay} />
          </div>
        );
      })}
    </div>
  );
}

function SmallMonth({
  month,
  busy,
  todayDay,
  selected,
  lang,
  onPick,
}: {
  month: Day;
  busy: Set<string>;
  todayDay: Day;
  selected: Day | null;
  lang: Lang;
  onPick: (day: Day) => void;
}) {
  return (
    <div className="grid grid-cols-7 gap-0.5 text-center text-[11px]">
      {Array.from({ length: 7 }, (_, weekday) => (
        <span key={weekday} className="py-1 text-[10px] text-faint">
          {weekdayName(weekday, lang, "narrow")}
        </span>
      ))}
      {monthGrid(month.y, month.m).map((day) => {
        const inMonth = day.m === month.m;
        const isToday = sameDay(day, todayDay);
        const isSelected = selected !== null && sameDay(day, selected) && !isToday;
        return (
          <button
            key={dayKey(day)}
            type="button"
            onClick={() => onPick(day)}
            className={cn(
              "relative h-[26px] rounded-full border-0 text-[11px]",
              !inMonth && "invisible",
              isToday ? "bg-accent font-semibold text-white" : isSelected ? "bg-accent/15 font-semibold text-accent-text" : "bg-transparent text-muted hover:bg-hover",
            )}
          >
            {day.d}
            {inMonth && busy.has(dayKey(day)) ? (
              <span className={cn("absolute bottom-0.5 left-1/2 -ml-0.5 h-1 w-1 rounded-full", isToday ? "bg-white" : "bg-accent")} />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function MiniCalendar({
  anchor,
  todayDay,
  items,
  tz,
  lang,
  label,
  onPick,
}: {
  anchor: Day;
  todayDay: Day;
  items: AgendaItem[];
  tz: string;
  lang: Lang;
  label: string;
  onPick: (day: Day) => void;
}) {
  const busy = useMemo(() => new Set(items.map((item) => dayKey(today(new Date(item.starts_at), tz)))), [items, tz]);
  const title = new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(anchor.y, anchor.m - 1, 1)),
  );
  return (
    <section aria-label={label} className="flex flex-col gap-1.5">
      <div className="flex items-center px-1 text-[13px] font-semibold">
        <span className="flex-1 capitalize">{title}</span>
      </div>
      <SmallMonth month={anchor} busy={busy} todayDay={todayDay} selected={anchor} lang={lang} onPick={onPick} />
    </section>
  );
}

const STATUS_CHIP: Record<string, string> = {
  running: "bg-accent/15 text-accent-text",
  success: "bg-success/15 text-success-text",
  failed: "bg-danger/15 text-danger-text",
  missed: "bg-danger/15 text-danger-text",
  to_confirm: "bg-warning/15 text-warning-text",
};

export function DetailCard({
  item,
  tz,
  lang,
  t,
  onAction,
}: {
  item: AgendaItem;
  tz: string;
  lang: Lang;
  t: TranslationDictionary;
  onAction: (action: ItemAction) => void;
}) {
  const text = t.planningV2;
  const start = zoned(new Date(item.starts_at), tz);
  const day = `${weekdayName(start.weekday, lang, "long")} ${start.d} ${new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(start.y, start.m - 1, 1)))}`;
  const statusText = item.status === "running" && item.progress_percent != null ? `${text.status.running} · ${Math.round(item.progress_percent)} %` : text.status[item.status];
  const actions = itemActions(item);
  return (
    <section aria-label={text.detail} className="flex flex-col gap-2 rounded-xl border border-solid border-border bg-surface p-3.5" data-detail={item.id}>
      <span className={cn("self-start rounded-full px-2 py-0.5 text-[11px] font-medium", STATUS_CHIP[item.status] ?? "bg-subtle text-muted")}>{statusText}</span>
      <span className="text-[15px] font-semibold">{itemLabel(item, t)}</span>
      <span className="text-xs text-muted">
        {day} · {timeOf(item.starts_at, tz)} – {timeOf(item.ends_at, tz)}
      </span>
      {item.kind === "proxmox" ? (
        <span className="font-mono text-xs text-faint" data-job={item.job_id ?? ""}>
          {[item.job_id, item.job_comment, item.message].filter(Boolean).join(" · ")}
        </span>
      ) : item.disk_label ? (
        <span className="text-xs text-faint">{item.disk_label}</span>
      ) : null}
      {item.message && item.status !== "success" && item.kind !== "proxmox" ? <span className="text-xs text-faint [overflow-wrap:anywhere]">{item.message}</span> : null}
      {actions.length ? (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {actions.map((action, index) => (
            <button
              key={action}
              type="button"
              onClick={() => onAction(action)}
              className={cn(
                "rounded-[7px] px-2.5 py-1.5 text-xs font-medium hover:brightness-110",
                index === 0 && action !== "edit" ? "border-0 bg-accent text-white" : "border border-solid border-border bg-subtle text-fg",
              )}
            >
              {text.action[action]}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ExportPopover({ t, loadFeed = getCalendarFeed, regenerate = regenerateCalendarFeed }: { t: TranslationDictionary; loadFeed?: () => Promise<CalendarFeed>; regenerate?: () => Promise<CalendarFeed> }) {
  const text = t.planningV2;
  const { toast } = useToast();
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [feed, setFeed] = useState<CalendarFeed | null>(null);
  const [history, setHistory] = useState(true);
  const [proxmox, setProxmox] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open && feed === null) void loadFeed().then(setFeed, () => setFeed({ enabled: false, url: null }));
  }, [open, feed, loadFeed]);

  const url = feed?.url ? feedUrl(feed.url, { history, proxmox }) : "";

  async function renew(ask: boolean) {
    if (ask && !(await confirm({ title: text.regenerateTitle, description: text.regenerateText, confirmLabel: text.regenerate, tone: "danger" }))) return;
    try {
      setFeed(await regenerate());
      if (ask) toast({ tone: "success", title: text.regenerated });
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    }
  }

  return (
    <Popover
      label={text.exportTitle}
      open={open}
      onOpenChange={setOpen}
      className="w-[min(380px,calc(100vw-32px))]"
      triggerClassName={cn(
        "flex items-center gap-2 rounded-lg border border-solid border-border-strong px-3 py-[7px] text-[13px] font-medium text-fg",
        open ? "bg-hover" : "bg-transparent hover:bg-hover",
      )}
      trigger={
        <>
          <Share className="h-4 w-4" />
          {text.export}
        </>
      }
    >
      <div className="flex flex-col gap-1 px-[18px] pb-3 pt-4">
        <span className="text-[15px] font-semibold">{text.exportTitle}</span>
      </div>
      <div className="flex flex-col gap-2 px-[18px] pb-3.5">
        <span className="text-[13px] font-medium">
          {text.subscribe}
          <span className="ml-1 rounded-full bg-accent/15 px-[7px] py-px text-[11px] font-medium text-accent-text">{text.recommended}</span>
        </span>
        <span className="text-xs leading-[1.45] text-muted">{text.subscribeHelp}</span>
        {feed?.enabled && url ? (
          <>
            <div className="flex gap-1.5">
              {/* No automatic clipboard write: the operator selects and copies the link. */}
              <input
                ref={input}
                readOnly
                aria-label={text.feedLabel}
                value={url}
                onFocus={(event) => event.currentTarget.select()}
                className="min-w-0 flex-1 rounded-lg border border-solid border-border bg-bg px-2.5 py-2 font-mono text-xs text-muted outline-none focus:border-accent"
              />
              <button
                type="button"
                onClick={() => {
                  input.current?.focus();
                  input.current?.select();
                }}
                className="whitespace-nowrap rounded-lg border-0 bg-accent px-3 py-2 text-[13px] font-medium text-white hover:brightness-110"
              >
                {text.selectLink}
              </button>
            </div>
            <span className="text-[11px] text-faint">{text.copyHint}</span>
            <span className="text-[11px] text-faint">{text.privateLink}</span>
          </>
        ) : feed ? (
          <button type="button" onClick={() => void renew(false)} className="self-start rounded-lg border-0 bg-accent px-3 py-2 text-[13px] font-medium text-white hover:brightness-110">
            {text.enableFeed}
          </button>
        ) : null}
      </div>
      <div className="flex flex-col gap-2 border-t border-solid border-border px-[18px] py-3">
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-muted">
          <input type="checkbox" checked={history} onChange={(event) => setHistory(event.target.checked)} className="m-0 h-4 w-4 accent-[rgb(var(--pbo-accent))]" />
          {text.includeHistory}
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-muted">
          <input type="checkbox" checked={proxmox} onChange={(event) => setProxmox(event.target.checked)} className="m-0 h-4 w-4 accent-[rgb(var(--pbo-accent))]" />
          {text.includeProxmox}
        </label>
      </div>
      {feed?.enabled && feed.url ? (
        <div className="flex flex-wrap gap-2 border-t border-solid border-border px-[18px] pb-4 pt-3">
            <a
              href={feedUrl(feed.url, { history, proxmox, download: true })}
              download="pbo.ics"
              className="flex items-center gap-2 rounded-lg border border-solid border-border bg-subtle px-3 py-2 text-[13px] font-medium text-fg no-underline hover:brightness-110"
            >
              <Download className="h-4 w-4" />
              {text.download}
            </a>
            <span className="flex-1" />
            <button type="button" onClick={() => void renew(true)} className="border-0 bg-transparent px-1 py-2 text-[13px] font-medium text-danger-text hover:underline">
              {text.regenerate}
            </button>
        </div>
      ) : null}
    </Popover>
  );
}

