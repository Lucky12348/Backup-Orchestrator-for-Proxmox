// Planning calendar (docs/design/mockups/planning.html): periods, grids,
// event placement, time zone maths, recurrence sentence. Pure (node:test).
//
// Every date shown is in the PLANNING time zone (the app TIMEZONE setting,
// returned by /planning/agenda), whatever the browser's zone is.

export type Lang = "fr" | "en";
export type CalendarView = "day" | "week" | "month" | "year";
export type ItemKind = "external" | "monthly" | "manual" | "proxmox";
export type ItemStatus =
  | "planned"
  | "waiting_disk"
  | "to_confirm"
  | "waiting"
  | "running"
  | "success"
  | "failed"
  | "missed"
  | "cancelled";

export const VIEWS: readonly CalendarView[] = ["day", "week", "month", "year"];
export const KINDS: readonly ItemKind[] = ["external", "monthly", "manual", "proxmox"];

export interface AgendaItem {
  id: string;
  kind: ItemKind;
  title: string | null;
  starts_at: string;
  ends_at: string;
  scheduled_for: string | null;
  window_ends_at: string | null;
  status: ItemStatus;
  event_id: number | null;
  planning_run_id: number | null;
  backup_run_id: number | null;
  disk_serial: string | null;
  disk_label: string | null;
  progress_percent: number | null;
  message: string | null;
  read_only: boolean;
}

/** A calendar day, independent of any time zone. */
export interface Day {
  y: number;
  m: number; // 1-12
  d: number;
}

export const DAY_MS = 24 * 3600 * 1000;

// --- Time zone maths ---------------------------------------------------------------

const partsCache = new Map<string, Intl.DateTimeFormat>();
function formatter(tz: string): Intl.DateTimeFormat {
  let value = partsCache.get(tz);
  if (!value) {
    value = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
    partsCache.set(tz, value);
  }
  return value;
}

export interface ZonedParts extends Day {
  h: number;
  min: number;
  /** 0 = Monday … 6 = Sunday */
  weekday: number;
}

export function zoned(date: Date, tz: string): ZonedParts {
  const parts = Object.fromEntries(formatter(tz).formatToParts(date).map((part) => [part.type, part.value]));
  const y = Number(parts.year);
  const m = Number(parts.month);
  const d = Number(parts.day);
  return { y, m, d, h: Number(parts.hour) % 24, min: Number(parts.minute), weekday: weekdayOf({ y, m, d }) };
}

function offsetMs(instant: number, tz: string): number {
  const minute = Math.floor(instant / 60000) * 60000;
  const p = zoned(new Date(minute), tz);
  return Date.UTC(p.y, p.m - 1, p.d, p.h, p.min) - minute;
}

/** Wall time in `tz` → the UTC instant (a time skipped by DST moves forward). */
export function zonedToUtc(day: Day, h: number, min: number, tz: string): Date {
  const guess = Date.UTC(day.y, day.m - 1, day.d, h, min);
  const first = guess - offsetMs(guess, tz);
  const second = guess - offsetMs(first, tz);
  return new Date(second);
}

// --- Calendar days -------------------------------------------------------------------

export function weekdayOf(day: Day): number {
  return (new Date(Date.UTC(day.y, day.m - 1, day.d)).getUTCDay() + 6) % 7;
}

export function addDays(day: Day, n: number): Day {
  const date = new Date(Date.UTC(day.y, day.m - 1, day.d) + n * DAY_MS);
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate() };
}

export function addMonths(day: Day, n: number): Day {
  const total = day.y * 12 + (day.m - 1) + n;
  const y = Math.floor(total / 12);
  const m = (total % 12) + 1;
  return { y, m, d: Math.min(day.d, daysInMonth(y, m)) };
}

export function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

export const sameDay = (a: Day, b: Day) => a.y === b.y && a.m === b.m && a.d === b.d;
export const dayKey = (day: Day) => `${day.y}-${String(day.m).padStart(2, "0")}-${String(day.d).padStart(2, "0")}`;

export function today(now: Date, tz: string): Day {
  const p = zoned(now, tz);
  return { y: p.y, m: p.m, d: p.d };
}

export function weekStart(day: Day): Day {
  return addDays(day, -weekdayOf(day));
}

/** Days shown by a view (week starts on Monday). Month: 6 rows trimmed to 5 when possible. */
export function visibleDays(view: CalendarView, anchor: Day): Day[] {
  if (view === "day") return [anchor];
  if (view === "week") return Array.from({ length: 7 }, (_, i) => addDays(weekStart(anchor), i));
  if (view === "month") return monthGrid(anchor.y, anchor.m);
  return Array.from({ length: 12 }, (_, i) => ({ y: anchor.y, m: i + 1, d: 1 }));
}

export function monthGrid(y: number, m: number): Day[] {
  const start = weekStart({ y, m, d: 1 });
  const cells = Array.from({ length: 42 }, (_, i) => addDays(start, i));
  return cells.slice(35).every((day) => day.m !== m) ? cells.slice(0, 35) : cells;
}

/** UTC range fetched for a view (whole days in the planning zone). */
export function viewRange(view: CalendarView, anchor: Day, tz: string): { start: Date; end: Date } {
  let first: Day;
  let last: Day;
  if (view === "year") {
    first = weekStart({ y: anchor.y, m: 1, d: 1 });
    last = addDays({ y: anchor.y, m: 12, d: 31 }, 7);
  } else {
    const days = visibleDays(view, anchor);
    first = days[0];
    last = days[days.length - 1];
  }
  return { start: zonedToUtc(first, 0, 0, tz), end: zonedToUtc(addDays(last, 1), 0, 0, tz) };
}

export function stepAnchor(view: CalendarView, anchor: Day, direction: 1 | -1): Day {
  if (view === "day") return addDays(anchor, direction);
  if (view === "week") return addDays(anchor, 7 * direction);
  if (view === "month") return addMonths({ ...anchor, d: 1 }, direction);
  return { y: anchor.y + direction, m: anchor.m, d: 1 };
}

const LOCALE = { fr: "fr-FR", en: "en-GB" };

function monthName(m: number, lang: Lang, style: "long" | "short" = "long"): string {
  return new Intl.DateTimeFormat(LOCALE[lang], { month: style, timeZone: "UTC" }).format(new Date(Date.UTC(2026, m - 1, 1)));
}

export function weekdayName(weekday: number, lang: Lang, style: "long" | "short" | "narrow" = "short"): string {
  // 2026-11-16 is a Monday.
  return new Intl.DateTimeFormat(LOCALE[lang], { weekday: style, timeZone: "UTC" }).format(new Date(Date.UTC(2026, 10, 16 + weekday)));
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** "15 – 21 nov. 2026", "lundi 16 novembre 2026", "Novembre 2026", "2026". */
export function periodTitle(view: CalendarView, anchor: Day, lang: Lang): string {
  if (view === "year") return String(anchor.y);
  if (view === "month") return capitalize(`${monthName(anchor.m, lang)} ${anchor.y}`);
  if (view === "day") {
    return capitalize(`${weekdayName(weekdayOf(anchor), lang, "long")} ${anchor.d} ${monthName(anchor.m, lang)} ${anchor.y}`);
  }
  const start = weekStart(anchor);
  const end = addDays(start, 6);
  if (start.m === end.m) return `${start.d} – ${end.d} ${monthName(end.m, lang, "short")} ${end.y}`;
  return `${start.d} ${monthName(start.m, lang, "short")} – ${end.d} ${monthName(end.m, lang, "short")} ${end.y}`;
}

// --- Items --------------------------------------------------------------------------------

export function itemDay(item: AgendaItem, tz: string): Day {
  return today(new Date(item.starts_at), tz);
}

export function itemsOn(items: readonly AgendaItem[], day: Day, tz: string): AgendaItem[] {
  return items.filter((item) => sameDay(itemDay(item, tz), day)).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
}

export function filterKinds(items: readonly AgendaItem[], hidden: ReadonlySet<ItemKind>): AgendaItem[] {
  return items.filter((item) => !hidden.has(item.kind));
}

export interface Placed {
  item: AgendaItem;
  /** Minutes since midnight (planning zone) and duration, for the time grid. */
  top: number;
  height: number;
  lane: number;
  lanes: number;
}

// Minutes: two readable lines in the time grid (34 px per hour).
const MIN_HEIGHT = 60;

/** Time-grid placement of one day's items; overlapping items share the width. */
export function placeDay(items: readonly AgendaItem[], day: Day, tz: string): Placed[] {
  const dayStart = zonedToUtc(day, 0, 0, tz).getTime();
  const dayEnd = zonedToUtc(addDays(day, 1), 0, 0, tz).getTime();
  const placed: Placed[] = itemsOn(items, day, tz).map((item) => {
    const start = Math.max(new Date(item.starts_at).getTime(), dayStart);
    const end = Math.min(Math.max(new Date(item.ends_at).getTime(), start + 15 * 60000), dayEnd);
    const local = zoned(new Date(start), tz);
    return { item, top: local.h * 60 + local.min, height: Math.max(MIN_HEIGHT, (end - start) / 60000), lane: 0, lanes: 1 };
  });
  const groups: Placed[][] = [];
  let current: Placed[] = [];
  let currentEnd = -1;
  for (const entry of placed) {
    if (entry.top >= currentEnd && current.length) {
      groups.push(current);
      current = [];
    }
    current.push(entry);
    currentEnd = Math.max(currentEnd, entry.top + entry.height);
  }
  if (current.length) groups.push(current);
  for (const group of groups) {
    const laneEnds: number[] = [];
    for (const entry of group) {
      const lane = laneEnds.findIndex((end) => end <= entry.top);
      entry.lane = lane === -1 ? laneEnds.length : lane;
      laneEnds[entry.lane] = entry.top + entry.height;
    }
    for (const entry of group) entry.lanes = laneEnds.length;
  }
  return placed;
}

/** Red "now" line: minutes since midnight in the planning zone, if `day` is today. */
export function nowMinutes(now: Date, day: Day, tz: string): number | null {
  const p = zoned(now, tz);
  return sameDay(p, day) ? p.h * 60 + p.min : null;
}

export type Tone = "planned" | "plannedMonthly" | "plannedManual" | "proxmox" | "success" | "failed" | "running" | "confirm" | "muted";

export function itemTone(item: AgendaItem): Tone {
  switch (item.status) {
    case "running":
      return "running";
    case "success":
      return item.kind === "proxmox" ? "proxmox" : "success";
    case "failed":
    case "missed":
      return "failed";
    case "to_confirm":
      return "confirm";
    case "cancelled":
      return "muted";
    default:
      return item.kind === "proxmox" ? "proxmox" : item.kind === "monthly" ? "plannedMonthly" : item.kind === "manual" ? "plannedManual" : "planned";
  }
}

export type ItemAction = "follow" | "run_now" | "report" | "edit" | "confirm" | "cancel";

/** Buttons of the detail panel (mockup: Suivre en direct / Lancer maintenant / Voir le rapport / Modifier). */
export function itemActions(item: AgendaItem): ItemAction[] {
  if (item.read_only) return [];
  const actions: ItemAction[] = [];
  if (item.status === "to_confirm" && item.planning_run_id) actions.push("confirm", "cancel");
  if (item.status === "running" && item.backup_run_id) actions.push("follow");
  else if ((item.status === "success" || item.status === "failed") && item.backup_run_id) actions.push("report");
  else if (item.event_id && (item.status === "planned" || item.status === "waiting_disk" || item.status === "missed")) actions.push("run_now");
  if (item.event_id) actions.push("edit");
  return actions;
}

// --- Planning form (create / edit dialog) ------------------------------------------------

export type Recurrence = "once" | "daily" | "weekly" | "monthly";

export interface PlanningForm {
  title: string;
  diskSerials: string[]; // rotation order
  recurrence: Recurrence;
  interval: number;
  /** First occurrence, wall time in the planning zone. */
  day: Day;
  hour: number;
  minute: number;
  windowMinutes: number;
  confirmation: boolean;
  autoEject: boolean;
  vmGroups: string[];
  datastore: string;
  enabled: boolean;
}

export interface EventLike {
  title: string;
  enabled: boolean;
  disk_serial: string;
  rotation_disk_serials?: string[] | null;
  vm_groups?: string[] | null;
  datastore: string;
  recurrence_type: Recurrence;
  recurrence_config: Record<string, unknown> | null;
  window_starts_at: string;
  window_duration_minutes: number;
  start_mode: "auto_on_disk_detected" | "manual_confirmation";
  auto_eject_after_success: boolean;
}

export function emptyForm(start: Date, tz: string, datastore: string): PlanningForm {
  const p = zoned(start, tz);
  return {
    title: "",
    diskSerials: [],
    recurrence: "weekly",
    interval: 1,
    day: { y: p.y, m: p.m, d: p.d },
    hour: p.h,
    minute: p.min - (p.min % 15),
    windowMinutes: 240,
    confirmation: false,
    autoEject: true,
    vmGroups: [],
    datastore,
    enabled: true,
  };
}

export function formFromEvent(event: EventLike, tz: string): PlanningForm {
  const p = zoned(new Date(event.window_starts_at), tz);
  const serials = event.rotation_disk_serials?.length ? event.rotation_disk_serials : [event.disk_serial];
  const interval = Number((event.recurrence_config ?? {}).interval ?? 1) || 1;
  return {
    title: event.title,
    diskSerials: [...serials],
    recurrence: event.recurrence_type,
    interval,
    day: { y: p.y, m: p.m, d: p.d },
    hour: p.h,
    minute: p.min,
    windowMinutes: event.window_duration_minutes,
    confirmation: event.start_mode === "manual_confirmation",
    autoEject: event.auto_eject_after_success,
    vmGroups: [...(event.vm_groups ?? [])],
    datastore: event.datastore,
    enabled: event.enabled,
  };
}

export type FormProblem = "title" | "disks" | "datastore" | "interval" | "window";

export function formProblems(form: PlanningForm): FormProblem[] {
  const problems: FormProblem[] = [];
  if (!form.title.trim()) problems.push("title");
  if (!form.diskSerials.length) problems.push("disks");
  if (!form.datastore.trim()) problems.push("datastore");
  if (!(form.interval >= 1 && form.interval <= 52)) problems.push("interval");
  if (!(form.windowMinutes >= 15 && form.windowMinutes <= 7 * 24 * 60)) problems.push("window");
  return problems;
}

/** Dialog → POST/PATCH /planning/events payload. The time is converted from the planning zone. */
export function formToPayload(form: PlanningForm, tz: string) {
  return {
    title: form.title.trim(),
    enabled: form.enabled,
    disk_serial: form.diskSerials[0],
    rotation_disk_serials: form.diskSerials,
    vm_groups: form.vmGroups,
    datastore: form.datastore.trim(),
    recurrence_type: form.recurrence,
    recurrence_config: form.recurrence === "once" ? null : { interval: form.interval },
    timezone: tz,
    window_starts_at: zonedToUtc(form.day, form.hour, form.minute, tz).toISOString(),
    window_duration_minutes: form.windowMinutes,
    start_mode: form.confirmation ? "manual_confirmation" : "auto_on_disk_detected",
    auto_eject_after_success: form.autoEject,
  } as const;
}

export interface SentenceTexts {
  once: string; // "Le {date} à {time}"
  daily: string; // "Chaque jour à {time}"
  dailyN: string; // "Tous les {n} jours à {time}"
  weekly: string; // "Chaque {weekday} à {time}"
  weeklyN: string; // "Un {weekday} sur {n} à {time}"
  monthly: string; // "Le {day} de chaque mois à {time}"
  monthlyN: string; // "Le {day}, tous les {n} mois, à {time}"
  onDisk: string; // ", sur {disk}"
  rotation: string; // ", sur {disks} en alternance"
  then: string; // " puis "
}

const pad = (value: number) => String(value).padStart(2, "0");

/** « Chaque dimanche à 09:00, sur WD 4 To puis Toshiba 3 To en alternance ». */
export function recurrenceSentence(form: PlanningForm, diskNames: readonly string[], texts: SentenceTexts, lang: Lang): string {
  const time = `${pad(form.hour)}:${pad(form.minute)}`;
  const weekday = weekdayName(weekdayOf(form.day), lang, "long");
  const n = String(form.interval);
  const date = `${form.day.d} ${monthName(form.day.m, lang)} ${form.day.y}`;
  const template = {
    once: texts.once,
    daily: form.interval > 1 ? texts.dailyN : texts.daily,
    weekly: form.interval > 1 ? texts.weeklyN : texts.weekly,
    monthly: form.interval > 1 ? texts.monthlyN : texts.monthly,
  }[form.recurrence];
  const base = template
    .replace("{time}", time)
    .replace("{weekday}", weekday)
    .replace("{n}", n)
    .replace("{day}", String(form.day.d))
    .replace("{date}", date);
  if (!diskNames.length) return base;
  if (diskNames.length === 1) return base + texts.onDisk.replace("{disk}", diskNames[0]);
  return base + texts.rotation.replace("{disks}", diskNames.join(texts.then));
}

/** Click on an empty slot → creation pre-filled with that day and hour. */
export function slotStart(day: Day, hour: number, tz: string): Date {
  return zonedToUtc(day, Math.max(0, Math.min(23, hour)), 0, tz);
}

/** iCal subscription / download URL with the export options. */
export function feedUrl(base: string, options: { history: boolean; proxmox: boolean; download?: boolean }): string {
  const url = new URL(base);
  if (options.history) url.searchParams.set("history", "true");
  else url.searchParams.delete("history");
  if (options.proxmox) url.searchParams.set("proxmox", "true");
  else url.searchParams.delete("proxmox");
  if (options.download) url.searchParams.set("download", "true");
  return url.toString();
}
